"""Router-brain: PydanticAI schema routing + analysis with keyword fallback.

Implements ARCHITECTURE.md section 2.1 (``Route`` + ``Analysis`` output models
and the four-step execution policy), DECISIONS.md D2 (temperature-0 schema
enforcement, one retry, deterministic keyword fallback, never-blocking) and
D16 (the ``analyze()`` task flavor with its own ledger ``kind``), plus:

- section 3.4: :meth:`RouterBrain.health_check` — the tiny one-call schema ping
  used by the supervisor boot pass (implemented HERE per plan R5).
- section 6 budget: the router prompt is capped (``_PROMPT_CHAR_CAP`` chars,
  the len-proxy TEST_PLAN row 11 asserts against ``2200 * 4 + margin``), the
  memory digests are ``MemoryEngine.digest`` projections (<= 600 chars each),
  the skill index is capped at 1500 chars, and at most the last-3 transcript
  events are sent — harness-side context stays O(1) per turn.
- section 7.3 (m4b): when a route() turn was decided by the keyword fallback
  the affected session gets one ``system`` transcript line with
  ``data={"note": "routed by fallback (keyword table)"}`` so the banner has
  data; the next message routes normally.
- the R5 helpers: :func:`long_session_guard` (the 25k-char compaction payload;
  the CALLER performs the fresh-session reset) and :func:`validate_handoff_block`
  (section 3.2 chat-scope ``HANDOFF`` fence parser, depth-3 advisory).

The mock seam (plan R5, REVIEW.md R-3) is a fake model OBJECT injected into
:meth:`RouterBrain.__init__` (``model=...``) — no httpx/transport patching
anywhere. Without a model the real provider path is built from
:func:`harness.config.effective_router_settings`:
``OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=..., api_key=...))``.

Blocking note (PoC, documented per the plan): :meth:`RouterBrain.route`,
:meth:`RouterBrain.analyze` and :meth:`RouterBrain.health_check` are synchronous
and run PydanticAI's ``run_sync`` — they BLOCK the event loop while the (tiny,
bounded) model call executes. This is acceptable for the PoC; the R6
orchestrator calls them from the loop and, when a fallback banner must be
appended to a live Session, prefers :meth:`RouterBrain.route_async` (which
performs the identical decision and awaits the transcript append).

Ledger: every call appends rows to ``<cfg.storage.data_dir>/state/router_ledger.jsonl``
through :class:`harness.persist.JsonlWriter` — one row per model attempt plus a
decision row on fallback (``kind`` in ``route|analyze|fallback|analyze_fallback|ping``,
``agent``, ``session_id``, ``fallback``, ``confidence``, ``latency_ms``, ``model``,
and ``error`` on failed attempts; audit rows count fallbacks independently from
attempts per plan R5). Ledger writes are best-effort and never raise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ValidationError, field_validator
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from harness.config import HarnessConfig, effective_router_settings
from harness.memory import MemoryEngine
from harness.persist import JsonlWriter, read_jsonl
from harness.session import Session
from harness.skills import SkillsEngine

__all__ = [
    "Analysis",
    "Route",
    "RouterBrain",
    "long_session_guard",
    "validate_handoff_block",
]

_LOG = logging.getLogger("harness.router")

# Fixed router rules: the first prompt section (plan R5: rules block ~15 lines).
_ROUTER_RULES = """\
You are the router of a 24/7 harness: pick the agent that handles the next turn.

## Rules
- agent: "opencode" for code tasks (implement, refactor, fix bugs, run tests, build tools);
  "hermes" for research and general chat (summarize, explain, plan, discuss).
- task_title: short title for the conversation thread (at most 80 characters).
- need_context: true only when the turn clearly needs prior transcript context.
- skill_hints: up to 3 skill names worth preloading; use [] when none apply.
- Emit ONLY the JSON object below. No prose, no code fence, lowercase names.

## Output contract
{"agent": "hermes" | "opencode", "task_title": "<str>", "need_context": <bool>,
 "skill_hints": ["<str>"]}
"""

# The D16 schema extension appended for the analyze() flavor.
_ANALYZE_SCHEMA = """\

## Analysis schema (task file analysis)
{"agent": "hermes" | "opencode", "mode": "oneway" | "interactive" | "gate",
 "workdir": "/app/projects/<name>", "prompt_seed": "<str, at most 800 chars>",
 "task_title": "<str>", "confidence": <0..1>, "need_context": <bool>,
 "skill_hints": ["<str>"]}
- pick agent for the TASK (code -> opencode, research/general -> hermes).
- mode "oneway" runs to completion; "interactive" shows the session in chat;
  "gate" only when a user question mid-task is expected (rare).
- workdir MUST start with "/app/projects"; use the task's own workdir when given.
- prompt_seed: the execution prompt the agent runner will start from.
"""

# Keyword fallback table (plan R5): route() ONLY; analyze() never keyword-maps.
_FALLBACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "opencode": ("code", "implement", "refactor", "bug", "fix"),
    "hermes": ("research", "summarize", "deploy", "chat"),
}

# Model-call exceptions that must NOT escape route()/analyze() (F4/F5: routing
# never blocks on provider problems). Programmer errors (ValueError on caller
# misuse) still propagate.
_CALL_ERRORS: tuple[type[BaseException], ...] = (
    UnexpectedModelBehavior,
    ModelHTTPError,
    ModelAPIError,
    httpx.HTTPError,
    httpx.InvalidURL,
    httpx.StreamError,
    OSError,
    TimeoutError,
    ValidationError,
)

_WORKDIR_ROOT = "/app/projects"
_TITLE_MAX = 80
_PROMPT_SEED_MAX = 800
_HINTS_MAX = 3
_SKILL_INDEX_CAP = 1500
_MAX_DIGEST_CHARS = 600
_EVENT_RENDER_CAP = 120
_LAST_EVENTS = 3
_PROMPT_CHAR_CAP = 9000  # the ~2.2k token len-proxy (TEST_PLAN row 11)
_MIN_INPUT_KEEP = 400
_SHORT_REASON_CAP = 120
_SUMMARY_CAP = 480
_LONG_SESSION_LIMIT = 25_000


def _utc_now() -> str:
    """ISO-8601 seconds + ``Z`` clock, matching :mod:`harness.session`."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(bus: Any | None, topic: str, payload: dict) -> None:
    """Best-effort bus emission (same shape as :mod:`harness.memory`).

    Fire-and-forget into a RUNNING event loop only; with no bus and/or no
    running loop it is a silent no-op — the sync route path never creates a
    loop. Bus failures inside the fired task never propagate to the caller.
    """
    if bus is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _publish() -> None:
        try:
            await bus.publish(topic, dict(payload))
        except Exception as exc:  # noqa: BLE001 — bus failures must not escape
            _LOG.warning("bus publish failed for %s: %s", topic, exc)

    loop.create_task(_publish())


def _keyword_fallback(text: str) -> str:
    """Deterministic keyword table (plan R5): opencode keywords first, default hermes."""
    lowered = (text or "").lower()
    for agent in ("opencode", "hermes"):
        for keyword in _FALLBACK_KEYWORDS[agent]:
            if keyword in lowered:
                return agent
    return "hermes"


def _clamp_fields(result: Any) -> Any:
    """Clamp advisory fields post-validation (never reject): title <=80, hints
    <=3, confidence 0..1, prompt_seed <=800 — on whatever fields the model has."""
    data = result.model_dump()
    changed = False
    title = data.get("task_title")
    if isinstance(title, str) and len(title) > _TITLE_MAX:
        data["task_title"] = title[:_TITLE_MAX]
        changed = True
    hints = data.get("skill_hints")
    if isinstance(hints, list) and len(hints) > _HINTS_MAX:
        data["skill_hints"] = hints[:_HINTS_MAX]
        changed = True
    confidence = data.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        clamped = min(1.0, max(0.0, float(confidence)))
        if clamped != confidence:
            data["confidence"] = clamped
            changed = True
    seed = data.get("prompt_seed")
    if isinstance(seed, str) and len(seed) > _PROMPT_SEED_MAX:
        data["prompt_seed"] = seed[:_PROMPT_SEED_MAX]
        changed = True
    if not changed:
        return result
    return type(result)(**data)


def _short_reason(exc: BaseException) -> str:
    """Short, one-line reason for ledger rows."""
    text = str(exc).strip().replace("\n", " ")
    if len(text) > _SHORT_REASON_CAP:
        text = text[:_SHORT_REASON_CAP]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _split_latency(total_ms: int, parts: int) -> list[int]:
    """Split ``total_ms`` into ``parts`` ints (sum preserved; last gets the rest)."""
    parts = max(parts, 1)
    total_ms = max(total_ms, 0)
    base = total_ms // parts
    rows = [base] * parts
    rows[-1] = total_ms - base * (parts - 1)
    return rows


def _retry_notes(messages: Any) -> list[str]:
    """Extract the validator-error snippets of every RetryPromptPart in order."""
    notes: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", None) or ():
            if isinstance(part, RetryPromptPart):
                snippet = json.dumps(part.content, default=str)
                notes.append(snippet[:_SHORT_REASON_CAP])
    return notes


def _part_snippet(content: Any) -> str:
    return json.dumps(content, default=str)[:_SHORT_REASON_CAP]


class Route(BaseModel):
    """A chat-turn routing decision (ARCHITECTURE.md section 2.1, frozen order)."""

    agent: Literal["hermes", "opencode"]
    task_title: str
    need_context: bool = False
    skill_hints: list[str] = []


class Analysis(BaseModel):
    """A task-analysis decision (DECISIONS.md D16, superset of Route)."""

    agent: Literal["hermes", "opencode"]
    mode: Literal["oneway", "interactive", "gate"] = "oneway"
    workdir: str
    prompt_seed: str
    task_title: str = ""
    confidence: float = 0.5
    need_context: bool = False
    skill_hints: list[str] = []

    @field_validator("workdir")
    @classmethod
    def _validate_workdir(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(_WORKDIR_ROOT):
            raise ValueError(f"workdir must start with '{_WORKDIR_ROOT}'")
        return value


def long_session_guard(final_text: str, *, limit: int = 25_000) -> dict | None:
    """Long-session guard payload (ARCHITECTURE.md section 6).

    Returns ``None`` while ``len(final_text) <= limit``; above the limit returns
    ``{"reset": True, "reason": ..., "summary": <first 4 lines joined by " | ",
    truncated to 480 chars>}``. PURE — the caller performs the actual
    fresh-session reset seeded with this summary.
    """
    if len(final_text) <= limit:
        return None
    lines = [line.strip("\r\n") for line in final_text.splitlines()]
    summary = " | ".join(lines[:4])
    if len(summary) > _SUMMARY_CAP:
        summary = summary[:_SUMMARY_CAP]
    return {
        "reset": True,
        "reason": f"final_text {len(final_text)} chars exceeds {limit}",
        "summary": summary,
    }


def validate_handoff_block(
    text: str, *, max_depth: int = 3, depth: int = 0
) -> dict | None:
    """Parse + validate one chat-scope ``HANDOFF`` fence (section 3.2, plan R5).

    Scans ``text`` for a fenced block opened with a line ```HANDOFF`` and
    closed by a matching ``` fence; yaml-parses the body and requires
    ``AGENT`` in {"hermes", "opencode"} and a string ``PROMPT`` of at most 800
    chars (unknown/extra keys are ignored). Returns ``{"agent", "prompt"}``
    when valid, ``None`` when absent/invalid/unclosed or when ``depth`` is at
    ``max_depth`` (refusal; the session manager enforces the real cap in R6+).
    """
    if depth >= max_depth:
        return None
    if not isinstance(text, str):
        return None
    lines = text.split("\n")
    body: list[str] | None = None
    for index, line in enumerate(lines):
        if line.strip() == "```HANDOFF":
            body = []
            for inner in lines[index + 1 :]:
                if inner.strip() == "```":
                    break
                body.append(inner)
            else:
                return None  # unclosed fence
            break
    if body is None:
        return None
    try:
        parsed = yaml.safe_load("\n".join(body))
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    agent = parsed.get("AGENT")
    if not isinstance(agent, str):
        return None
    agent = agent.strip().lower()
    if agent not in ("hermes", "opencode"):
        return None
    prompt = parsed.get("PROMPT")
    if not isinstance(prompt, str) or len(prompt) > _PROMPT_SEED_MAX:
        return None
    return {"agent": agent, "prompt": prompt}


def _render_event(record: dict) -> str:
    """Compact last-3-events projection: ``kind: <first 120 chars of json>``."""
    blob = json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"{record.get('kind')}: {blob[:_EVENT_RENDER_CAP]}"


def _cap_prompt(head: str, input_text: str) -> str:
    """Assemble the prompt under the char cap, ALWAYS keeping the input.

    The memory/skill/events sections are individually capped before this runs;
    here the backstop first trims the head (rules+digests+index+events), keeps
    the whole input while it fits, and only when the input itself floods the
    cap keeps its head (marked). The final global clamp is the documented
    ``2200 tok * 4 + margin`` len-proxy the TEST_PLAN row 11 asserts.
    """
    head = head.strip()
    input_text = input_text.strip()
    joiner = "\n\n"
    if len(head) + len(joiner) + len(input_text) <= _PROMPT_CHAR_CAP:
        return head + joiner + input_text if head else input_text
    keep_head = _PROMPT_CHAR_CAP - _MIN_INPUT_KEEP - 40
    if len(head) > keep_head:
        head = head[:keep_head]
    remaining = max(_PROMPT_CHAR_CAP - len(head) - len(joiner) - 40, _MIN_INPUT_KEEP)
    if len(input_text) > remaining:
        input_text = input_text[:remaining] + "\n…[input truncated]"
    prompt = head + joiner + input_text
    if len(prompt) > _PROMPT_CHAR_CAP:
        prompt = prompt[:_PROMPT_CHAR_CAP]
    return prompt


class RouterBrain:
    """The only LLM the harness calls: two PydanticAI agents over one model.

    Built over the config provider model (or an injected model object in
    tests); two lazy agents share it — one with ``output_type=Route`` for
    :meth:`route`, one with ``output_type=Analysis`` for :meth:`analyze` —
    both ``ModelSettings(temperature=0)`` and ``retries=cfg.router.max_retries``
    clamped to a sane >=0 int (the retry appends the validator error itself as
    a RetryPromptPart; nothing is hand-appended). Every model attempt and the
    fallback decisions are ledgered; provider/validator failures fall back
    deterministically and NEVER raise (F4/F5).
    """

    def __init__(self, cfg: HarnessConfig, bus: Any | None = None, model: Any | None = None) -> None:
        self._cfg = cfg
        self._bus = bus
        if model is None:
            settings = effective_router_settings(cfg)
            model = OpenAIChatModel(
                settings["model"],
                provider=OpenAIProvider(base_url=settings["base_url"], api_key=settings["api_key"]),
            )
        self._model = model
        self._model_name = str(getattr(model, "model_name", "") or "") or cfg.router.model
        self._retries = max(0, int(cfg.router.max_retries))
        self._model_settings: ModelSettings = ModelSettings(temperature=0)
        self._route_agent: Agent | None = None
        self._analyze_agent: Agent | None = None
        self._ledger_path = Path(cfg.storage.data_dir) / "state" / "router_ledger.jsonl"
        self._ledger = JsonlWriter(self._ledger_path)

    # ------------------------------------------------------------------
    # Lazy agent construction
    # ------------------------------------------------------------------
    @property
    def _agent_route(self) -> Agent:
        if self._route_agent is None:
            self._route_agent = Agent(
                model=self._model,
                output_type=Route,
                model_settings=self._model_settings,
                retries=self._retries,
            )
        return self._route_agent

    @property
    def _agent_analyze(self) -> Agent:
        if self._analyze_agent is None:
            self._analyze_agent = Agent(
                model=self._model,
                output_type=Analysis,
                model_settings=self._model_settings,
                retries=self._retries,
            )
        return self._analyze_agent

    # ------------------------------------------------------------------
    # Prompt builder (private; sections in the contract order)
    # ------------------------------------------------------------------
    def _memory_section(self, *, include_lessons: bool = False) -> str:
        """Digest projections (<=600 chars each); lessons only when asked for."""
        try:
            engine = MemoryEngine(self._cfg, self._bus)
            parts: list[str] = []
            for name, label in (("MEMORY", "MEMORY"), ("USER", "USER"), ("lessons", "lessons")):
                if name == "lessons" and not include_lessons:
                    continue
                digest = engine.digest(name)[:_MAX_DIGEST_CHARS]
                if digest:
                    parts.append(f"{label}:\n{digest}")
        except Exception as exc:  # noqa: BLE001 — digests must never block routing
            _LOG.warning("router prompt: memory digests unavailable: %s", exc)
            return ""
        return "\n\n".join(parts)

    def _skill_section(self) -> str:
        """The generated skill index text (<=1500 chars); omitted on error."""
        try:
            engine = SkillsEngine(self._cfg)
            index = engine.build_index()[:_SKILL_INDEX_CAP]
        except Exception as exc:  # noqa: BLE001 — a broken skills tree never blocks routing
            _LOG.warning("router prompt: skill index unavailable: %s", exc)
            return ""
        return index

    def _events_section(self, session: Session | None) -> str:
        """The last 3 transcript records, compactly rendered; never raises."""
        if session is None:
            return ""
        try:
            records = read_jsonl(session.transcript_path)
        except Exception as exc:  # noqa: BLE001 — an unreadable transcript never blocks routing
            _LOG.warning("router prompt: transcript unreadable: %s", exc)
            return ""
        return "\n".join(_render_event(record) for record in records[-_LAST_EVENTS:])

    def _build_route_prompt(
        self,
        text: str,
        *,
        session: Session | None = None,
        need_skill_index: bool | None = None,
        include_memory: bool = True,
        include_skills: bool = True,
        include_events: bool = True,
        include_lessons: bool = False,
        rules: str = _ROUTER_RULES,
    ) -> str:
        """Assemble the prompt (sections: rules, memory, skills, events, input)."""
        sections: list[str] = [rules.strip()]
        if include_memory:
            memory = self._memory_section(include_lessons=include_lessons)
            if memory:
                sections.append(f"## Memory\n{memory}")
        if include_skills and need_skill_index is not False:
            skills = self._skill_section()
            if skills:
                sections.append(f"## Skills\n{skills}")
        if include_events:
            events = self._events_section(session)
            if events:
                sections.append(f"## Recent session events\n{events}")
        head = "\n\n".join(sections)
        return _cap_prompt(head, f"## Input\n{text}")

    def _build_analyze_prompt(
        self,
        task_desc: str,
        *,
        workdir: str,
        session: Session | None = None,
    ) -> str:
        """Same sections as route(), plus the D16 schema and lessons digests."""
        rules = _ROUTER_RULES.rstrip() + _ANALYZE_SCHEMA
        task_title = f"## Task (workdir default when none given: {workdir})\n{task_desc}"
        sections: list[str] = [rules.strip()]
        memory = self._memory_section(include_lessons=True)
        if memory:
            sections.append(f"## Memory\n{memory}")
        skills = self._skill_section()
        if skills:
            sections.append(f"## Skills\n{skills}")
        events = self._events_section(session)
        if events:
            sections.append(f"## Recent session events\n{events}")
        head = "\n\n".join(sections)
        return _cap_prompt(head, task_title)

    def _build_ping_prompt(self) -> str:
        """Tiny one-route ping (section 3.4): rules + minimal input, no disk reads."""
        return self._build_route_prompt(
            "one-route ping: reply with the JSON route object (agent of your choice).",
            include_memory=False,
            include_skills=False,
            include_events=False,
        )

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------
    def ledger_rows(self) -> list[dict]:
        """The ledger contents (torn-tail tolerant reader; tests use this)."""
        return read_jsonl(self._ledger_path)

    def _ledger_row(
        self,
        *,
        kind: str,
        session_id: str | None,
        fallback: bool,
        confidence: float | None,
        latency_ms: int,
        agent: str | None = None,
        error: str | None = None,
    ) -> None:
        """Append one audit row; best-effort, never raises."""
        row: dict[str, Any] = {
            "ts": _utc_now(),
            "kind": kind,
            "agent": agent,
            "session_id": session_id,
            "fallback": fallback,
            "confidence": confidence,
            "latency_ms": latency_ms,
            "model": self._model_name,
        }
        if error is not None:
            row["error"] = error
        try:
            self._ledger.append(row)
        except Exception as exc:  # noqa: BLE001 — disk full (F7) must not block routing
            _LOG.warning("router ledger append failed: %s", exc)

    # ------------------------------------------------------------------
    # Decision core (shared machinery of route()/analyze())
    # ------------------------------------------------------------------
    def _ledger_success(
        self,
        run: Any,
        *,
        attempt_kind: str,
        session_id: str | None,
        elapsed_ms: int,
    ) -> tuple[Any, bool]:
        """Ledger one row per attempt for a schema-valid run; the final attempt
        carries ``fallback=false``, the retried ones the validator-error note."""
        requests = max(1, int(run.usage.requests or 1))
        result = _clamp_fields(run.output)
        notes = _retry_notes(run.all_messages())
        latencies = _split_latency(elapsed_ms, requests)
        for index in range(requests - 1):
            self._ledger_row(
                kind=attempt_kind,
                session_id=session_id,
                fallback=True,
                confidence=None,
                latency_ms=latencies[index],
                error=notes[index] if index < len(notes) else "output validation retry",
            )
        self._ledger_row(
            kind=attempt_kind,
            session_id=session_id,
            fallback=False,
            confidence=getattr(result, "confidence", None),
            latency_ms=latencies[-1],
            agent=result.agent,
        )
        return result, False

    def _ledger_failure(
        self,
        exc: BaseException,
        *,
        attempt_kind: str,
        decision_kind: str,
        session_id: str | None,
        fallback_result: Callable[[], Any],
        elapsed_ms: int,
    ) -> tuple[Any, bool]:
        """Ledger the failed attempts + the deterministic fallback decision row."""
        reason = _short_reason(exc)
        attempts = self._retries + 1 if isinstance(exc, UnexpectedModelBehavior) else 1
        for latency in _split_latency(elapsed_ms, attempts):
            self._ledger_row(
                kind=attempt_kind,
                session_id=session_id,
                fallback=True,
                confidence=None,
                latency_ms=latency,
                error=reason,
            )
        decided = fallback_result()
        self._ledger_row(
            kind=decision_kind,
            session_id=session_id,
            fallback=True,
            confidence=getattr(decided, "confidence", None),
            latency_ms=elapsed_ms,
            agent=decided.agent,
        )
        return decided, True

    def _decide(
        self,
        agent: Agent,
        prompt: str,
        *,
        attempt_kind: str,
        decision_kind: str,
        session_id: str | None,
        fallback_result: Callable[[], Any],
    ) -> tuple[Any, bool]:
        """Sync-flavor version of the schema-enforced call (see _ledger_*)."""
        started = time.perf_counter()
        try:
            run = agent.run_sync(prompt)
        except _CALL_ERRORS as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return self._ledger_failure(
                exc,
                attempt_kind=attempt_kind,
                decision_kind=decision_kind,
                session_id=session_id,
                fallback_result=fallback_result,
                elapsed_ms=elapsed,
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        return self._ledger_success(run, attempt_kind=attempt_kind, session_id=session_id,
                                    elapsed_ms=elapsed)

    async def _decide_async(
        self,
        agent: Agent,
        prompt: str,
        *,
        attempt_kind: str,
        decision_kind: str,
        session_id: str | None,
        fallback_result: Callable[[], Any],
    ) -> tuple[Any, bool]:
        """Async-flavor version of :meth:`_decide` (route_async runs in a live
        loop, where ``run_sync`` cannot; PydanticAI's ``agent.run`` is awaited)."""
        started = time.perf_counter()
        try:
            run = await agent.run(prompt)
        except _CALL_ERRORS as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return self._ledger_failure(
                exc,
                attempt_kind=attempt_kind,
                decision_kind=decision_kind,
                session_id=session_id,
                fallback_result=fallback_result,
                elapsed_ms=elapsed,
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        return self._ledger_success(run, attempt_kind=attempt_kind, session_id=session_id,
                                    elapsed_ms=elapsed)

    def _fallback_route(self, text: str) -> Route:
        agent = _keyword_fallback(text)
        title = text.strip().split("\n", 1)[0][:_TITLE_MAX]
        return Route(agent=agent, task_title=title, need_context=False, skill_hints=[])

    def _fallback_analysis(self, task_desc: str, workdir: str) -> Analysis:
        title = task_desc.strip().split("\n", 1)[0][:_TITLE_MAX]
        seed = task_desc if len(task_desc) <= _PROMPT_SEED_MAX else task_desc[:_PROMPT_SEED_MAX]
        return Analysis(
            agent="hermes",
            mode="oneway",
            workdir=workdir,
            prompt_seed=seed,
            task_title=title,
            confidence=0.5,
            need_context=False,
            skill_hints=[],
        )

    # ------------------------------------------------------------------
    # route() — the chat-turn flavor
    # ------------------------------------------------------------------
    def _route_core(
        self,
        text: str,
        *,
        session: Session | None,
        session_manager: Any | None,
        need_skill_index: bool | None,
    ) -> tuple[Route, bool]:
        """Shared decision body of route()/route_async() (banner handled by wrappers)."""
        del session_manager  # R6 wiring passes it; signature parity only
        prompt = self._build_route_prompt(text, session=session, need_skill_index=need_skill_index)
        result, engaged = self._decide(
            self._agent_route,
            prompt,
            attempt_kind="route",
            decision_kind="fallback",
            session_id=session.id if session is not None else None,
            fallback_result=partial(self._fallback_route, text),
        )
        if session is not None:
            _emit(self._bus, "route.decided", {"session_id": session.id, "route": result.agent})
        return result, engaged

    def route(
        self,
        text: str,
        *,
        session: Session | None = None,
        session_manager: Any | None = None,
        need_skill_index: bool | None = None,
    ) -> Route:
        """Route a chat turn to hermes/opencode; NEVER raises for provider problems.

        Sync flavor: on fallback-with-session the banner transcript line fires
        ONLY when an event loop is already running (fire-and-forget task) —
        with no loop it is skipped. Prefer :meth:`route_async` when the
        orchestrator awaits this from a loop (identical decision, awaited
        banner). Note: this call blocks the loop during the model call (PoC).
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("route() requires non-empty text")
        result, engaged = self._route_core(
            text,
            session=session,
            session_manager=session_manager,
            need_skill_index=need_skill_index,
        )
        if engaged and session is not None:
            self._schedule_banner(session)
        return result

    async def route_async(
        self,
        text: str,
        *,
        session: Session | None = None,
        session_manager: Any | None = None,
        need_skill_index: bool | None = None,
    ) -> Route:
        """Awaitable flavor: identical decision; the fallback banner IS awaited."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("route() requires non-empty text")
        del session_manager  # R6 wiring passes it; signature parity only
        prompt = self._build_route_prompt(text, session=session, need_skill_index=need_skill_index)
        result, engaged = await self._decide_async(
            self._agent_route,
            prompt,
            attempt_kind="route",
            decision_kind="fallback",
            session_id=session.id if session is not None else None,
            fallback_result=partial(self._fallback_route, text),
        )
        if session is not None:
            _emit(self._bus, "route.decided", {"session_id": session.id, "route": result.agent})
        if engaged and session is not None:
            await self._banner_event(session)
        return result

    # ------------------------------------------------------------------
    # Banner (m4b R5 half)
    # ------------------------------------------------------------------
    @staticmethod
    async def _banner_event(session: Session) -> None:
        """Append the fallback banner transcript line; best-effort."""
        try:
            await session.append_event("system", {"note": "routed by fallback (keyword table)"})
        except Exception as exc:  # noqa: BLE001 — a failed banner never breaks routing
            _LOG.warning("fallback banner append failed for %s: %s", session.id, exc)

    def _schedule_banner(self, session: Session) -> None:
        """Fire the banner coroutine into the RUNNING loop; no loop -> skip."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._banner_event(session))

        def _done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                _LOG.warning("fallback banner task failed: %s", exc)

        task.add_done_callback(_done)

    # ------------------------------------------------------------------
    # analyze() — the task-file flavor (D16)
    # ------------------------------------------------------------------
    def analyze(
        self,
        task_desc: str,
        *,
        workdir: str | None = None,
        session: Session | None = None,
    ) -> Analysis:
        """Analyze a task description; analysis NEVER blocks the loop (D16).

        On exhausted schema retries the deterministic fallback analysis wins:
        (hermes, oneway, ``workdir`` default). This call blocks the loop
        during the model call (PoC).
        """
        if not isinstance(task_desc, str) or not task_desc.strip():
            raise ValueError("analyze() requires non-empty task_desc")
        default_workdir = (
            workdir if workdir is not None else str(self._cfg.agents.opencode.workdir_root)
        )
        prompt = self._build_analyze_prompt(task_desc, workdir=default_workdir, session=session)
        result, _engaged = self._decide(
            self._agent_analyze,
            prompt,
            attempt_kind="analyze",
            decision_kind="analyze_fallback",
            session_id=session.id if session is not None else None,
            fallback_result=partial(self._fallback_analysis, task_desc, default_workdir),
        )
        return result

    # ------------------------------------------------------------------
    # health_check() — boot ping (section 3.4, round-1 m4a)
    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        """Tiny one-call schema ping; ``True`` on a valid Route, False on ANY
        failure. Never raises; side-effect-free apart from one ``kind=ping``
        ledger row (the supervisor boot pass primes the fallback with this)."""
        started = time.perf_counter()
        agent: str | None = None
        error: str | None = None
        try:
            run = self._agent_route.run_sync(self._build_ping_prompt())
            result = _clamp_fields(run.output)
            if isinstance(result, Route):
                agent = result.agent
            else:
                error = "unexpected output type"
        except Exception as exc:  # noqa: BLE001 — the ping must never raise
            error = _short_reason(exc)
        ok = error is None and agent is not None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._ledger_row(
            kind="ping",
            session_id=None,
            fallback=not ok,
            confidence=None,
            latency_ms=elapsed_ms,
            agent=agent,
            error=error,
        )
        return ok
