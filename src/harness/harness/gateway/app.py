"""Harness HTTP gateway: the FastAPI app factory (ARCHITECTURE.md sections
2.7, 5.2, 5.3 and 7.2; DECISIONS.md D6/D8; plan phase R6).

:func:`create_app(cfg, bus, manager, *, router_brain=None, adapters=None,
test_limiter=None, taskloop_=None)` wires:

- **Auth (D6/5.2)** — ``X-API-Key`` (``hmac.compare_digest`` against
  ``cfg.api_key``; ``401`` + ``WWW-Authenticate: APIKey``) AND a signed
  ``harness_session`` HttpOnly cookie exchanged once via ``POST /api/login``
  (value = ``HMAC-SHA256(api_key, "harness-session-v1")`` hex, derived once per
  process and cached; never carries the raw key; no client nonce storage — a
  documented PoC simplification). ``GET /`` always renders the static page; the
  page's inline JS asks for the key and performs the exchange.
- **404-not-403 (5.3)** — every id-bearing route validates the id against the
  generic ``^[a-zA-Z0-9-_]{1,64}$`` guard (a superset of the 8-char base32
  alphabet) BEFORE any lookup; guard misses and unknown sessions both return
  404 (no existence oracle, no traversal).
- **Rate limits (5.2/5.3)** — slowapi with in-memory storage keyed by remote
  address: ``cfg.rate_limit.per_minute``/minute on the writes (chat/login/
  handoff), ``cfg.rate_limit.stream_per_minute``/minute on the SSE stream.
- **Routes** — see the section 2.7 table (repo-adjusted names): healthz, page,
  vendored htmx asset, chat turn, SSE stream, session meta list, transcript
  tail, materials report (7.2: sizes/counts/names only — NEVER file contents,
  env values, or raw ledger rows), task list/detail meta routes + the
  WAITING_INPUT gate answer write (2.11/D18; repo-adjusted answer path) and
  the internal handoff proxy.
- **Streaming contract** — the bus is NOT replayable: an SSE connection
  receives only events published while subscribed (``?seq=`` resume drops
  already-seen ``reply.chunk`` events received on that same connection). Agent
  runs are spawned as tasks (never awaited in the request); their chunks and
  finals fan out on the bus AND land on the transcript (transcript ⊇ SSE).
- **M-5b** — the reverse-proxy/``--proxy-headers`` fold lives in
  ``harness.supervisor._serve`` driven by ``cfg.server.behind_proxy``; this
  module only reads it (nothing here is socket-level).
- **Default wiring** — with no injected ``router_brain`` a real
  ``RouterBrain(cfg, bus=bus)`` is constructed (outside tests); with no
  ``adapters`` mapping the ``harness.adapters`` registry lazily constructs the
  real hermes/opencode adapter per agent name on first use — the PoC gateway
  only GUARANTEES the injected fake path end-to-end. ``taskloop_`` is the
  task-loop instance behind ``POST /api/tasks/answer`` (supervisor wiring);
  when ``None`` that write route answers ``503 task loop not wired`` while
  the read-only task routes still serve from the TaskStore.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from yaml import safe_load as yaml_safe_load

from harness.adapters import get as registry_get
from harness.adapters.base import AgentRun, RunSpec
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.errors import SessionFSMError
from harness.gateway.ui import render_page
from harness.memory import MemoryEngine
from harness.persist import read_jsonl
from harness.router import Route, RouterBrain, validate_handoff_block
from harness.session import Session
from harness.session_manager import SessionManager
from harness.skills import SkillsEngine
from harness.tasks import TaskSchemaError, TaskStore

__all__ = ["create_app"]

_LOG = logging.getLogger("harness.gateway")

# The generic 5.3 id guard (superset of base32 — kept uniform across id routes).
_ID_GUARD = re.compile(r"^[a-zA-Z0-9-_]{1,64}$")

_COOKIE_NAME = "harness_session"
_COOKIE_MESSAGE = b"harness-session-v1"
_COOKIE_MAX_AGE_S = 30 * 24 * 60 * 60  # 30 days (D6)
_LOGIN_FAILURE = "invalid or missing API key"
_TITLE_MAX = 80

# R5 runs ledger lands with R9/R10; until then the presence check only.
_RUNS_LEDGER_FILENAME = "runs.jsonl"

_HTMX_STATIC = Path(__file__).resolve().parent / "static" / "htmx.min.js"

_session_cookie_cache: dict[str, str] = {}


def _session_cookie_value(api_key: str) -> str:
    """The signed cookie value: ``HMAC-SHA256(api_key, "harness-session-v1")``,
    derived once per process and cached; NEVER the raw key."""
    value = _session_cookie_cache.get(api_key)
    if value is None:
        value = hmac.new(api_key.encode("utf-8"), _COOKIE_MESSAGE, "sha256").hexdigest()
        _session_cookie_cache[api_key] = value
    return value


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail=_LOGIN_FAILURE, headers={"WWW-Authenticate": "APIKey"}
    )


def _not_found(detail: str = "not found") -> HTTPException:
    """404-not-403 (5.3): the ONLY id-route lookup failure."""
    return HTTPException(status_code=404, detail=detail)


class ChatIn(BaseModel):
    """JSON body of ``POST /api/chat`` (the HTMX form path is parsed manually)."""

    text: str
    session_id: str | None = None


class HandoffIn(BaseModel):
    """JSON body of ``POST /api/handoff``."""

    session_id: str
    text: str


class AnswerIn(BaseModel):
    """JSON body of ``POST /api/tasks/answer/{task_id}`` (2.11/D18)."""

    text: str


def create_app(
    cfg: HarnessConfig,
    bus: Bus,
    manager: SessionManager,
    *,
    router_brain: RouterBrain | None = None,
    adapters: (
        Mapping[str, object]
        | Callable[[str], object]
        | None
    ) = None,
    test_limiter: Limiter | None = None,
    taskloop_: object | None = None,
) -> FastAPI:
    """Build the full R6 gateway over the shared ``cfg``/``bus``/``manager``.

    ``router_brain`` defaults to ``RouterBrain(cfg, bus=bus)`` (the real model
    path, constructed outside tests). ``adapters`` accepts a mapping
    (agent name -> adapter-ish object, or a factory callable taking the agent
    name) or a single factory callable; ``None`` lazily resolves through the
    :mod:`harness.adapters` registry with the full config. ``test_limiter``
    swaps the in-memory slowapi limiter (tests injecting one otherwise build
    their own app). ``taskloop_`` (R10/D18) wires ``POST /api/tasks/answer``;
    with ``None`` the gate answer write answers 503 ``task loop not wired``
    (the task LIST/DETAIL read routes still work through a local
    :class:`~harness.tasks.TaskStore`).
    """
    limiter = test_limiter if test_limiter is not None else Limiter(key_func=get_remote_address)
    brain = router_brain if router_brain is not None else RouterBrain(cfg, bus=bus)
    api_key = cfg.api_key
    cookie_value = _session_cookie_value(api_key)
    task_store = TaskStore(cfg)

    app = FastAPI(title="harness-gateway")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.state.agent_tasks: set[asyncio.Task] = set()

    adapter_cache: dict[str, AgentRun] = {}

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def require_api_key(
        key: str | None = Security(api_key_header),
    ) -> None:
        """D6/5.2: the bare header dependency (constant-time compare)."""
        if key is None or not hmac.compare_digest(key, api_key):
            raise _unauthorized()

    async def require_auth(
        request: Request, key: str | None = Security(api_key_header)
    ) -> None:
        """Route auth: valid ``X-API-Key`` header OR valid signed cookie."""
        if key is not None and hmac.compare_digest(key, api_key):
            return
        cookie = request.cookies.get(_COOKIE_NAME)
        if cookie is not None and hmac.compare_digest(cookie, cookie_value):
            return
        raise _unauthorized()

    def _resolve_adapter(agent: str) -> AgentRun:
        """Adapter for ``agent`` (mapping / factory / registry), cached per app."""
        cached = adapter_cache.get(agent)
        if cached is not None:
            return cached
        if adapters is None:
            adapter: AgentRun = registry_get(agent, cfg)
        elif callable(adapters):
            adapter = adapters(agent)  # type: ignore[assignment]
        else:
            entry = adapters.get(agent)
            if entry is None:
                raise HTTPException(status_code=503, detail=f"no adapter wired for {agent!r}")
            # A bare factory function (no run()) is called with the agent name;
            # an adapter-ish object is used verbatim.
            adapter = entry(agent) if callable(entry) and not hasattr(entry, "run") else entry
        adapter_cache[agent] = adapter
        return adapter

    def _assemble_prompt(session: Session, route: Route, text: str) -> str:
        """Chat prompt: memory digests when ``route.need_context`` and skill
        bodies for ``route.skill_hints`` (both best-effort — engine failures
        degrade to the raw input), then the input always last."""
        del session  # the digest engines read the shared data_dir, not the transcript
        sections: list[str] = []
        if route.need_context:
            try:
                memory_engine = MemoryEngine(cfg, bus)
                digests = [memory_engine.digest(name) for name in ("MEMORY", "USER", "lessons")]
                memory = "\n".join(digest for digest in digests if digest)
                if memory:
                    sections.append(f"### Memory digest\n{memory}")
            except Exception as exc:  # noqa: BLE001 — enrichment must never block a run
                _LOG.warning("gateway prompt: memory digests unavailable: %s", exc)
        if route.skill_hints:
            try:
                skill_engine = SkillsEngine(cfg)
                bodies = []
                for name in route.skill_hints:
                    body = skill_engine.get_skill_body(name)
                    if body:
                        bodies.append(f"#### skill: {name}\n{body}")
                if bodies:
                    sections.append("### Skills\n" + "\n\n".join(bodies))
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("gateway prompt: skill bodies unavailable: %s", exc)
        sections.append(f"## Input\n{text}")
        return "\n\n".join(sections)

    def _transcript_length(session: Session) -> int:
        try:
            return len(read_jsonl(session.transcript_path))
        except (OSError, ValueError):
            return 0

    def _just_fell_back(session: Session, pre_len: int) -> bool:
        """Whether route_async appended the R5 fallback banner (a ``system``
        transcript note) since ``pre_len`` — the m4b web marker is derived from
        the ledger-backed transcript banner data."""
        try:
            records = read_jsonl(session.transcript_path)[pre_len:]
        except (OSError, ValueError):
            return False
        for record in records:
            if record.get("kind") == "system":
                note = str((record.get("data") or {}).get("note", ""))
                if "routed by fallback" in note:
                    return True
        return False

    async def _run_agent(session: Session, adapter: AgentRun, spec: RunSpec, route: Route) -> None:
        """One spawned agent run (chat or handoff): relay the stream onto the
        bus + transcript, then move the session ``relaying`` -> ``idle``."""
        started = time.perf_counter()
        seq = 0

        async def _chunk(text: str) -> None:
            nonlocal seq
            seq += 1
            await bus.publish(
                "reply.chunk",
                {"session_id": session.id, "seq": seq, "text": text, "source": "agent"},
            )
            await session.append_event("agent_chunk", {"seq": seq, "text": text, "source": "agent"})

        async def _final(text: str, *, failed: bool) -> None:
            meta: dict = {
                "agent": route.agent,
                "cost": None,
                "duration_s": round(time.perf_counter() - started, 3),
            }
            if failed:
                meta["failed"] = True
            await bus.publish("reply.final", {"session_id": session.id, "text": text, "meta": meta})
            await session.append_event("agent_final", {"text": text, "meta": meta})

        terminal_seen = False
        try:
            async for event in adapter.run(spec):
                if event.kind == "chunk":
                    await _chunk(event.text or "")
                elif event.kind == "tool":
                    brief = event.tool_brief or event.tool_name or "?"
                    await _chunk(f"[tool] {brief}")
                elif event.kind == "final":
                    await _final(event.final_text or "", failed=False)
                    terminal_seen = True
                    break
                elif event.kind == "failed":
                    await _final(f"[run failed] {event.error or 'unknown error'}", failed=True)
                    terminal_seen = True
                    break
                elif event.kind in ("started", "system"):
                    continue  # not relayed (documented PoC contract)
            if not terminal_seen:
                await _final("[run failed] adapter stream ended without a final event", failed=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — honest terminal, never crash
            _LOG.exception("agent run failed for session %s", session.id)
            await _final(f"[run failed] internal error: {exc}", failed=True)
        finally:
            try:
                await manager.move(session, "relaying")
                await manager.move(session, "idle")
            except SessionFSMError as exc:
                _LOG.warning("session %s post-run moves skipped: %s", session.id, exc)

    def _spawn(session: Session, adapter: AgentRun, spec: RunSpec, route: Route) -> None:
        task = asyncio.create_task(_run_agent(session, adapter, spec, route))
        app.state.agent_tasks.add(task)
        task.add_done_callback(app.state.agent_tasks.discard)

    # ------------------------------------------------------------------
    # Unauthenticated routes
    # ------------------------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        """Minimal liveness: ``{"ok": true}`` — no version, no extra fields."""
        return {"ok": True}

    @app.get("/")
    async def index() -> Response:
        """The UI page. Served to ANY caller (cookie/key/none — the fall-through
        case carries the ``?auth=1`` prompt in-page): the page's inline JS does
        the key-for-cookie exchange via ``POST /api/login`` (D6)."""
        return render_page()

    @app.get("/static/htmx.min.js")
    async def htmx_static() -> FileResponse:
        """The vendored htmx asset; NO auth (the page needs it before login)."""
        return FileResponse(_HTMX_STATIC, media_type="application/javascript")

    @app.post("/api/login")
    @limiter.limit(lambda: f"{cfg.rate_limit.per_minute}/minute")
    async def login(request: Request) -> Response:
        """Exchange the API key (JSON ``{"api_key": ...}`` or a form field) for
        the signed HttpOnly cookie (30-day max-age, samesite=lax); constant-time
        compare; mismatch -> 401 + ``WWW-Authenticate: APIKey``. Rate-limited as
        a write."""
        key: object | None = None
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith("application/json"):
            body = await request.body()
            try:
                parsed = json.loads(body) if body else {}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="invalid JSON body") from exc
            key = parsed.get("api_key") if isinstance(parsed, dict) else None
        elif content_type.startswith("application/x-www-form-urlencoded"):
            form = await request.form()
            key = form.get("api_key")
        else:
            raise HTTPException(status_code=415, detail="use JSON or form-encoded login")
        if not isinstance(key, str) or not hmac.compare_digest(key, api_key):
            raise _unauthorized()
        response = JSONResponse({"ok": True})
        response.set_cookie(
            _COOKIE_NAME,
            cookie_value,
            max_age=_COOKIE_MAX_AGE_S,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    # ------------------------------------------------------------------
    # Chat turn
    # ------------------------------------------------------------------
    @app.post("/api/chat", dependencies=[Depends(require_auth)])
    @limiter.limit(lambda: f"{cfg.rate_limit.per_minute}/minute")
    async def chat(request: Request) -> Response:
        """One chat turn: auth -> id guard -> get_or_create -> route_async ->
        route line (+ fallback marker) -> spawn the adapter run task; the run
        streams via ``GET /api/chat/stream``. htmx callers receive a route-line
        fragment carrying ``data-session-id``; regular callers the JSON."""
        if brain is None:  # pragma: no cover — constructor always provides one
            raise HTTPException(status_code=503, detail="router unavailable")

        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith("application/x-www-form-urlencoded"):
            form = await request.form()
            text = str(form.get("text") or "")
            raw_session_id = str(form.get("session_id") or "").strip() or None
        else:
            try:
                chat_in = ChatIn.model_validate_json(await request.body())
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="invalid chat body") from exc
            text = chat_in.text
            raw_session_id = (chat_in.session_id or "").strip() or None
        if not text or not text.strip():
            raise HTTPException(status_code=422, detail="text must be non-empty")

        if raw_session_id is not None and _ID_GUARD.fullmatch(raw_session_id) is None:
            raise _not_found()
        try:
            session = await manager.get_or_create(raw_session_id, channel_handle=None)
        except ValueError as exc:
            raise _not_found("unknown session id") from exc
        created_new = raw_session_id is None
        await session.append_event("user", {"text": text})

        if session.state not in ("created", "idle", "failed", "routing"):
            raise HTTPException(status_code=409, detail=f"session busy: {session.state}")
        pre_len = _transcript_length(session)
        try:
            if session.state != "routing":
                await manager.move(session, "routing")
            route = await brain.route_async(text, session=session, session_manager=manager)
        except SessionFSMError as exc:
            raise HTTPException(status_code=409, detail=f"illegal session transition: {exc}") from exc

        fallback_engaged = _just_fell_back(session, pre_len)
        adapter = _resolve_adapter(route.agent)  # 503 honestly BEFORE the route line
        route_line = f"[routed to {route.agent}] {route.task_title}"
        await bus.publish(
            "reply.chunk",
            {"session_id": session.id, "seq": 0, "text": route_line, "source": "router"},
        )
        await session.append_event("router", {"agent": route.agent, "task_title": route.task_title})
        if fallback_engaged:
            await bus.publish(
                "reply.chunk",
                {
                    "session_id": session.id,
                    "seq": None,
                    "text": "routed by fallback rules",
                    "source": "system",
                },
            )
        if created_new and route.task_title:
            session.rename(route.task_title[:_TITLE_MAX])
        await manager.move(session, "agent_running")

        spec = RunSpec(
            prompt=_assemble_prompt(session, route, text),
            profile=None,
            workdir=None,  # Route carries no workdir (R5): analysis workdirs land R9/R10
            resume_ref=session.agents_refs.get(route.agent),
            timeout_s=getattr(cfg.agents, route.agent).timeout_s,
        )
        _spawn(session, adapter, spec, route)

        if request.headers.get("hx-request"):
            fragment = (
                f'<div class="route-line" data-session-id="{session.id}"'
                f' data-fallback="{str(fallback_engaged).lower()}">'
                f"{html.escape(route_line)}</div>"
            )
            return HTMLResponse(fragment)
        return JSONResponse(
            {"session_id": session.id, "state": session.state, "route": route.model_dump()}
        )

    # ------------------------------------------------------------------
    # SSE stream
    # ------------------------------------------------------------------
    @app.get("/api/chat/stream", dependencies=[Depends(require_auth)])
    @limiter.limit(lambda: f"{cfg.rate_limit.stream_per_minute}/minute")
    async def chat_stream(
        request: Request,
        session_id: str = Query(...),
        seq: int | None = Query(default=None),
        heartbeat_s: float = Query(default=15.0),
    ) -> EventSourceResponse:
        """SSE relay of one session's bus traffic: ``reply.chunk`` / ``reply.final``
        / ``session.status`` (``system.log`` NEVER exposed). ``heartbeat_s`` is a
        TEST override in ``(0, 60]`` seconds (default 15.0); ``seq`` drops already-
        seen chunks received on THIS connection (the bus is not replayable)."""
        if _ID_GUARD.fullmatch(session_id) is None:
            raise _not_found()
        try:
            await manager.get_or_create(session_id, channel_handle=None)
        except ValueError as exc:
            raise _not_found("unknown session id") from exc
        if heartbeat_s <= 0 or heartbeat_s > 60:
            raise HTTPException(status_code=422, detail="heartbeat_s must be in (0, 60]")

        async def event_source() -> AsyncIterator[dict]:
            sink_name = f"sse:{session_id}:{uuid.uuid4().hex}"
            queue = bus.subscribe(sink_name)
            internal_id = 0
            try:
                while True:
                    try:
                        topic, payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                    except TimeoutError:
                        yield {"event": "heartbeat", "data": ":"}
                        continue
                    if not isinstance(payload, dict) or payload.get("session_id") != session_id:
                        continue
                    if topic == "reply.chunk":
                        chunk_seq = payload.get("seq")
                        if seq is not None and isinstance(chunk_seq, int) and chunk_seq <= seq:
                            continue
                        internal_id += 1
                        event_id = str(chunk_seq) if isinstance(chunk_seq, int) else str(internal_id)
                        yield {
                            "event": "reply.chunk",
                            "id": event_id,
                            "data": json.dumps(
                                {
                                    "session_id": session_id,
                                    "seq": chunk_seq,
                                    "text": payload.get("text"),
                                    "source": payload.get("source"),
                                }
                            ),
                        }
                    elif topic == "reply.final":
                        internal_id += 1
                        yield {
                            "event": "reply.final",
                            "id": str(internal_id),
                            "data": json.dumps(payload),
                        }
                    elif topic == "session.status":
                        internal_id += 1
                        yield {
                            "event": "session.status",
                            "id": str(internal_id),
                            "data": json.dumps(payload),
                        }
                    # system.log and every other topic are NEVER exposed here.
            finally:
                bus.unsubscribe(sink_name)

        return EventSourceResponse(event_source())

    # ------------------------------------------------------------------
    # Session meta + transcript tail
    # ------------------------------------------------------------------
    @app.get("/api/sessions", dependencies=[Depends(require_auth)])
    async def list_sessions(archived: bool | None = Query(default=None)) -> Response:
        """Meta-only listing (no transcript content): one row per session dir,
        unreadable metas skipped; ``?archived=true|false`` (default excludes)."""
        rows: list[dict] = []
        root = manager.sessions_root
        if root.is_dir():
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    continue
                row = _meta_row(directory, str(directory.name))
                if row is None:
                    continue  # unreadable meta: skipped per the 7.2 contract
                if archived is None:
                    if row["archived"]:
                        continue
                elif row["archived"] != archived:
                    continue
                rows.append(row)
        return JSONResponse(rows)

    def _meta_row(directory: Path, fallback_id: str) -> dict | None:
        """Meta-only row ``{session_id, state, title, created, updated, archived}``;
        ``None`` when meta.json is missing/unreadable/non-mapping."""
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict):
            return None
        return {
            "session_id": str(meta.get("session_id") or fallback_id),
            "state": meta.get("state"),
            "title": meta.get("title"),
            "created": meta.get("created"),
            "updated": meta.get("updated"),
            "archived": bool(meta.get("archived")),
        }

    @app.get("/api/session/{session_id}", dependencies=[Depends(require_auth)])
    async def session_transcript(
        session_id: str, tail: int = Query(default=100, ge=1, le=500)
    ) -> Response:
        """Transcript TAIL (``?tail=`` 1..500, default 100). ``../``/``%2e%2e``
        ids fail the guard first; unknown ids are 404 like any other."""
        if _ID_GUARD.fullmatch(session_id) is None:
            raise _not_found()
        directory = manager.sessions_root / session_id
        if directory.parent != manager.sessions_root or not directory.is_dir():
            raise _not_found()
        meta = _meta_row(directory, session_id)
        if meta is None:
            raise _not_found("unknown session id")
        try:
            events = read_jsonl(directory / "transcript.jsonl")[-tail:]
        except (OSError, ValueError):
            events = []
        return JSONResponse(
            {
                "session_id": session_id,
                "title": meta["title"],
                "state": meta["state"],
                "events": events,
            }
        )

    # ------------------------------------------------------------------
    # Tasks: meta list/detail (2.9) + WAITING_INPUT gate answer (2.11/D18)
    # ------------------------------------------------------------------
    def _task_state(task_id: str) -> dict | None:
        """state.json of a picked-up task via the app-local TaskStore
        (corrupt/missing -> None). Ids beyond the guard stay 404."""
        try:
            return task_store.read_state(task_id)
        except (OSError, TypeError, ValueError):
            return None

    def _task_yaml_raw(task_id: str) -> dict:
        """Best-effort task.yaml mapping (skip-on-unreadable contract)."""
        try:
            raw = yaml_safe_load(
                (task_store.root / task_id / "task.yaml").read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            raw = None
        return raw if isinstance(raw, dict) else {}

    @app.get("/api/tasks", dependencies=[Depends(require_auth)])
    async def list_tasks() -> Response:
        """Meta-only task listing (2.9/7.2): ``{id, state, title, depth,
        parent, updated}`` — title = task.yaml ``description[:80]``. No
        events, no result contents, no gate payloads, no raw ledger rows.
        Unreadable tasks are skipped; capped at 200 rows sorted by id."""
        rows: list[dict] = []
        try:
            ids = sorted(task_store.list_tasks())
        except OSError:
            ids = []
        for task_id in ids[:200]:
            state_data = _task_state(task_id)
            if state_data is None:
                continue  # unreadable/missing state: skipped per the 7.2 contract
            raw = _task_yaml_raw(task_id)
            if not raw:
                continue  # unreadable task.yaml: skipped
            description = raw.get("description")
            rows.append(
                {
                    "id": task_id,
                    "state": state_data.get("state"),
                    "title": description[:_TITLE_MAX]
                    if isinstance(description, str)
                    else "",
                    "depth": state_data.get("depth"),
                    "parent": state_data.get("parent"),
                    "updated": state_data.get("updated"),
                }
            )
        return JSONResponse(rows)

    @app.get("/api/task/{task_id}", dependencies=[Depends(require_auth)])
    async def task_detail(task_id: str) -> Response:
        """One task's meta detail (2.9/7.2): the SAFE task.yaml fields
        (description/origin/parent/depends_on/oneway + state.json depth), the
        last 100 events.jsonl lines, and result.md PRESENCE + size only.
        NEVER file contents beyond the task.yaml fields, NEVER gate payloads
        or raw ledger rows."""
        if _ID_GUARD.fullmatch(task_id) is None:
            raise _not_found()
        state_data = _task_state(task_id)
        if state_data is None:
            raise _not_found("unknown task id")
        raw = _task_yaml_raw(task_id)
        description = raw.get("description")
        depends_on = raw.get("depends_on")
        oneway = raw.get("oneway")
        parent = state_data.get("parent")
        if not isinstance(parent, str) or not parent:
            parent = raw.get("parent") if isinstance(raw.get("parent"), str) else None
        origin = state_data.get("origin")
        if not isinstance(origin, str) or not origin:
            origin = raw.get("origin") if isinstance(raw.get("origin"), str) else None
        try:
            events = read_jsonl(task_store.root / task_id / "events.jsonl")[-100:]
        except (OSError, ValueError):
            events = []
        result_path = task_store.root / task_id / "result.md"
        result_present = result_path.is_file()
        try:
            result_bytes = result_path.stat().st_size if result_present else None
        except OSError:  # pragma: no cover — race between is_file and stat
            result_present, result_bytes = False, None
        return JSONResponse(
            {
                "task_id": task_id,
                "state": state_data.get("state"),
                "task": {
                    "description": description if isinstance(description, str) else None,
                    "origin": origin,
                    "parent": parent,
                    "depth": state_data.get("depth"),
                    "depends_on": [
                        dep for dep in depends_on if isinstance(dep, str)
                    ] if isinstance(depends_on, list) else [],
                    "oneway": oneway if isinstance(oneway, bool) else False,
                },
                "events": events,
                "result_md": result_present,
                "result_md_bytes": result_bytes,
            }
        )

    @app.post("/api/tasks/answer/{task_id}", dependencies=[Depends(require_auth)])
    @limiter.limit(lambda: f"{cfg.rate_limit.per_minute}/minute")
    async def answer_task(request: Request, task_id: str) -> Response:
        """2.11/D18 gate answer: hand the text to ``taskloop.answer`` (the
        SAME resume path the Discord ``gate.answered`` event uses). Id guard
        + unknown task are 404 (never 403); a non-WAITING_INPUT task or a
        gate that is not live in this process (restart) is 409; no wired task
        loop is 503 ``task loop not wired``. Success: ``{ok, task_id,
        state}``."""
        if taskloop_ is None:
            raise HTTPException(status_code=503, detail="task loop not wired")
        if _ID_GUARD.fullmatch(task_id) is None:
            raise _not_found()
        state_data = _task_state(task_id)
        if state_data is None:
            raise _not_found("unknown task id")
        if state_data.get("state") != "WAITING_INPUT":
            raise HTTPException(
                status_code=409,
                detail=f"task {task_id}: not WAITING_INPUT "
                f"(state {state_data.get('state')}); cannot answer",
            )
        try:
            answer_in = AnswerIn.model_validate_json(await request.body())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid answer body") from exc
        if not answer_in.text or not answer_in.text.strip():
            raise HTTPException(status_code=422, detail="text must be non-empty")
        try:
            await taskloop_.answer(task_id, answer_in.text)
        except TaskSchemaError as exc:  # gate died in the meantime (restart)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        fresh = _task_state(task_id) or {}
        return JSONResponse({"ok": True, "task_id": task_id, "state": fresh.get("state")})

    # ------------------------------------------------------------------
    # Handoff proxy
    # ------------------------------------------------------------------
    @app.post("/api/handoff", dependencies=[Depends(require_auth)])
    @limiter.limit(lambda: f"{cfg.rate_limit.per_minute}/minute")
    async def handoff(request: Request) -> Response:
        """Internal handoff: validate the 3.2 ``HANDOFF`` fence at the parent's
        depth (R5 helper), spawn the child via ``create_child``, route + run it;
        invalid/over-depth -> 422 with the reason."""
        try:
            handoff_in = HandoffIn.model_validate_json(await request.body())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid handoff body") from exc
        session_id = handoff_in.session_id
        if not handoff_in.text or not handoff_in.text.strip():
            raise HTTPException(status_code=422, detail="text must be non-empty")
        if _ID_GUARD.fullmatch(session_id) is None:
            raise _not_found()
        try:
            session = await manager.get_or_create(session_id, channel_handle=None)
        except ValueError as exc:
            raise _not_found("unknown session id") from exc
        parsed = validate_handoff_block(
            handoff_in.text, max_depth=cfg.handoff.max_depth, depth=session.depth
        )
        if parsed is None:
            raise HTTPException(status_code=422, detail="invalid or over-depth handoff block")
        try:
            child = await manager.create_child(session, parsed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SessionFSMError as exc:
            raise HTTPException(status_code=409, detail=f"illegal session transition: {exc}") from exc
        prompt = str(parsed.get("prompt") or "")
        try:
            route = await brain.route_async(prompt, session=child, session_manager=manager)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        adapter = _resolve_adapter(route.agent)
        route_line = f"[routed to {route.agent}] {route.task_title}"
        await bus.publish(
            "reply.chunk",
            {"session_id": child.id, "seq": 0, "text": route_line, "source": "router"},
        )
        await child.append_event("router", {"agent": route.agent, "task_title": route.task_title})
        await manager.move(child, "agent_running")
        spec = RunSpec(
            prompt=_assemble_prompt(child, route, prompt),
            profile=None,
            workdir=None,
            resume_ref=child.agents_refs.get(route.agent),
            timeout_s=getattr(cfg.agents, route.agent).timeout_s,
        )
        _spawn(child, adapter, spec, route)
        return JSONResponse(
            {
                "session_id": child.id,
                "parent_session": session.id,
                "state": child.state,
                "route": route.model_dump(),
            }
        )

    # ------------------------------------------------------------------
    # Materials report (7.2)
    # ------------------------------------------------------------------
    def _materials_report() -> tuple[str, dict]:
        """The 7.2 live report: names/counts/sizes only — NEVER file contents,
        env values, or raw ledger rows (5.3 negatives)."""
        data_dir = Path(cfg.storage.data_dir)
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        session_rows: list[dict] = []
        sessions_root = data_dir / "sessions"
        if sessions_root.is_dir():
            for directory in sorted(sessions_root.iterdir()):
                if not directory.is_dir():
                    continue
                row = _meta_row(directory, str(directory.name))
                if row is not None:
                    session_rows.append(row)

        memory_rows: list[dict] = []
        budgets = cfg.memory.budgets
        memory_root = data_dir / "memory"
        for filename, budget_key in (
            ("MEMORY.md", "memory_chars"),
            ("USER.md", "user_chars"),
            ("lessons.md", "lessons_chars"),
        ):
            file_path = memory_root / filename
            if not file_path.is_file():
                continue
            try:
                chars = len(file_path.read_text(encoding="utf-8"))
            except OSError as exc:  # pragma: no cover — unreadable memory is skipped
                _LOG.warning("materials: %s unreadable: %s", file_path, exc)
                continue
            memory_rows.append(
                {"file": filename, "chars": chars, "budget": int(getattr(budgets, budget_key))}
            )

        skill_names: list[str] = []
        index_chars = 0
        skills_root = data_dir / "skills"
        if skills_root.is_dir():
            skill_names = sorted(entry.name for entry in skills_root.iterdir() if entry.is_dir())
            index_path = skills_root / "INDEX.md"
            if index_path.is_file():
                try:
                    index_chars = len(index_path.read_text(encoding="utf-8"))
                except OSError:
                    index_chars = 0

        ledger_calls = 0
        ledger_fallbacks = 0
        ledger_path = data_dir / "state" / "router_ledger.jsonl"
        if ledger_path.is_file():
            try:
                rows = read_jsonl(ledger_path)
            except (OSError, ValueError):
                rows = []
            today_rows = [row for row in rows if str(row.get("ts", "")).startswith(today)]
            ledger_calls = len(today_rows)
            ledger_fallbacks = sum(1 for row in today_rows if row.get("fallback") is True)

        runs_rows: list[dict] = []
        runs_total = 0
        runs_path = data_dir / "state" / _RUNS_LEDGER_FILENAME
        if runs_path.is_file():
            try:
                ledger = read_jsonl(runs_path)
            except (OSError, ValueError):
                ledger = []
            runs_total = len(ledger)
            # Summaries only: ts / id / state — never the raw JSONL rows.
            runs_rows = [
                {
                    "ts": str(row.get("ts", "")),
                    "session_id": row.get("session_id") or row.get("task_id"),
                    "state": row.get("state") or row.get("outcome"),
                }
                for row in ledger[-20:]
            ]

        report_lines = [
            f"harness materials ({today})",
            f"sessions: {len(session_rows)}",
            *(
                f"  {row['session_id']} {row['state']}{' (archived)' if row['archived'] else ''}"
                for row in session_rows
            ),
            "memory:",
            *(
                (f"  {row['file']}: {row['chars']} chars (budget {row['budget']})" for row in memory_rows)
                if memory_rows
                else ("  (no memory files)",)
            ),
            f"skills: {len(skill_names)} skill(s); INDEX.md {index_chars} chars",
            *(f"  {name}" for name in skill_names),
            f"router: today calls={ledger_calls} fallback={ledger_fallbacks}",
        ]
        if runs_path.is_file():
            report_lines.append(f"runs: {len(runs_rows)} of {runs_total} row(s)")
            report_lines.extend(
                f"  - {row['ts']} {row['session_id'] or '?'} {row['state'] or ''}"
                for row in runs_rows
            )
        # Tasks section (R10): live per-state counts from the TaskStore (the
        # app-local store is used even when no task loop is wired — same
        # data_dir). Counts only: no event/result contents, no gate payloads,
        # no raw gates.jsonl rows (gates NEVER touch /materials). The runs
        # ledger listing above is populated by the taskloop runner rows.
        task_counts: dict[str, int] = {}
        try:
            task_states = task_store.list_tasks()
        except OSError:
            task_states = {}
        for state in task_states.values():
            if isinstance(state, str):
                task_counts[state] = task_counts.get(state, 0) + 1
        tasks_total = sum(task_counts.values())
        if tasks_total:
            by_state = ", ".join(f"{state} {count}" for state, count in sorted(task_counts.items()))
            report_lines.append(f"tasks: {tasks_total} ({by_state})")
        else:
            report_lines.append("tasks: 0")

        payload = {
            "generated": today,
            "sessions": session_rows,
            "memory": memory_rows,
            "skills": {"index_chars": index_chars, "names": skill_names},
            "router": {"calls": ledger_calls, "fallback": ledger_fallbacks},
            "runs": runs_rows if runs_path.is_file() else None,
            "tasks": {"total": tasks_total, "by_state": dict(sorted(task_counts.items()))},
        }
        return "\n".join(report_lines), payload

    @app.get("/materials", dependencies=[Depends(require_auth)])
    async def materials(
        request: Request, output_format: str = Query(default="text", alias="format")
    ) -> Response:
        """7.2 materials report — plain human text by default, JSON with
        ``?format=json``; sizes/counters only, never contents or ledger rows."""
        del request  # present for route symmetry with the other authed routes
        report, payload = _materials_report()
        if output_format == "json":
            return JSONResponse(payload)
        return Response(report, media_type="text/plain")

    return app
