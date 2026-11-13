"""Discord bot adapter (ARCHITECTURE.md section 2.8, plan phase R7).

The bot is ONE bus subscription (design 2.8): :meth:`DiscordBot.start` registers
sink ``discord-bot`` and :meth:`DiscordBot._pump` drains its queue, turning
harness traffic into Discord sends. Inbound ``on_message`` mirrors the R6
gateway turn pipeline (:mod:`harness.gateway.app`): allowlist (REVIEW.md R-6)
-> ``/new`` / ``/status`` / ``answer T<id> ...`` gate answers (the bus publish
IS the contract; the gate engine wires in at R10) -> chat turn with the same
route line / fallback banner / adapter stream relay / FSM moves.

Parity notes (deliberate, documented):
- ``_run_agent`` is a line-level mirror of the gateway's closure-private relay
  loop with ONE documented deviation: a ``final``/``failed`` StreamEvent
  carrying a truthy ``session_ref`` is recorded via
  ``Session.set_agent_ref`` (the R7 turn loop owns the session; the gateway
  closure does not, but the hermes/opencode adapters emit refs).
- ``_assemble_prompt`` duplicates the gateway helper (memory digests when
  ``route.need_context``, skill bodies for ``route.skill_hints``, input last);
  R12 may extract a shared builder.
- Discord is fire-and-forget (F8): every send/edit failure is logged and
  dropped — the web gateway is unaffected. The pump never crashes.
- Replies to the user carry NO env values and NEVER the bot token (5.3).

Client surface (the ONLY client calls bot logic may make — tests inject a
fake with the same six): ``client.user``, ``await send_message(channel,
text)``, ``await edit_message(message, text)``, ``get_channel(id)``,
``await fetch_channel(id)``, ``await create_thread(channel, name)``.
``spawn_bot`` builds the REAL client (``discord.py`` 2.7.1) behind a small
``_RealClient`` wrapper; the heavy ``import discord`` happens inside
``spawn_bot`` only so the module imports lightweight and testable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from harness.adapters import get as registry_get
from harness.adapters.base import RunSpec
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.errors import SessionFSMError
from harness.memory import MemoryEngine
from harness.persist import read_jsonl
from harness.router import Route, RouterBrain
from harness.session import Session
from harness.session_manager import SessionManager
from harness.skills import SkillsEngine

__all__ = ["GATE_ANSWER_PATTERN", "HOME_CHANNEL_ENVS", "DiscordBot", "spawn_bot"]

_LOG = logging.getLogger("harness.gateway.discord")

_SINK = "discord-bot"
_TITLE_MAX = 80
_STATUS_MAX = 1900  # Discord message ceiling the /status report targets
_ROUTE_CHUNK_CHARS = 1900  # chunk_chars fallback mirror == cfg.discord.chunk_chars default
_NOTICE_DROP_LOG_INTERVAL_S = 60.0
_FIRST_CONNECT_GRACE_S = 15.0
_HOME_CHANNEL_ENVS: tuple[str, ...] = ("HARNESS_DISCORD_HOME_CHANNEL", "DISCORD_HOME_CHANNEL")
GATE_ANSWER_PATTERN: re.Pattern[str] = re.compile(r"^answer\s+T(\S+)\s+(.+)$")
HOME_CHANNEL_ENVS = _HOME_CHANNEL_ENVS
_ALLOWED_STATES = frozenset({"created", "idle", "failed", "routing"})
_BUSY_STATES = frozenset({"created", "routing", "agent_running", "relaying", "agent_failed"})
_FAILED_STATES = frozenset({"failed", "agent_failed"})
_TERMINAL_TASK_STATES = frozenset({"done", "failed", "cancelled", "skipped", "archived"})


class _StreamState:
    """Per-session edit-in-place stream buffer (placeholder message + timing)."""

    __slots__ = ("buffer", "edit_task", "message", "next_edit_at")

    def __init__(self) -> None:
        self.buffer: str = ""
        self.message: object | None = None
        self.next_edit_at: float = 0.0
        self.edit_task: asyncio.Task[None] | None = None


class DiscordBot:
    """The Discord surface of the harness: one inbound message path, one pump.

    ``client`` is the injected six-method shim (real wrapper built by
    :func:`spawn_bot`, fakes in tests). ``brain`` lazily builds the real
    ``RouterBrain`` on first use when not injected; ``adapters`` mirrors the
    gateway seam (None -> registry, mapping/factory verbatim, cached per agent).
    ``clock`` is a zero-arg monotonic-seconds callable (default loop.time).
    """

    def __init__(
        self,
        cfg: HarnessConfig,
        bus: Bus,
        manager: SessionManager,
        *,
        client: object,
        brain: RouterBrain | None = None,
        adapters: Mapping[str, object] | Callable[[str], object] | None = None,
        clock: Callable[[], float] | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._manager = manager
        self._client = client
        self._adapters = adapters
        self._adapter_cache: dict[str, object] = {}
        self._brain = brain
        self._clock = clock if clock is not None else self._loop_time
        self._log = log if log is not None else _LOG
        self._stream: dict[str, _StreamState] = {}
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._session_channels: dict[str, int] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._bus_queue: asyncio.Queue[tuple[str, dict]] | None = None
        self._last_notice_drop = float("-inf")

    @staticmethod
    def _loop_time() -> float:
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:  # pragma: no cover — pre-loop bookkeeping only
            return time.monotonic()

    @property
    def turn_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._turn_tasks)

    async def wait_turns(self) -> None:
        """Await all spawned turn tasks, then yield the loop (test convenience).

        Public, documented, test-friendly surface: the pump still needs a few
        scheduling ticks to drain its queue after the last turn task exits.
        """
        if self._turn_tasks:
            await asyncio.gather(*list(self._turn_tasks), return_exceptions=False)
        for _ in range(4):
            await asyncio.sleep(0)

    async def start(self) -> None:
        self._bus_queue = self._bus.subscribe(_SINK)
        self._pump_task = asyncio.get_running_loop().create_task(self._pump())
        self._log.info("discord bot listening (home channel: %s)", self._home_channel())

    async def stop(self) -> None:
        self._stopping = True
        tasks: list[asyncio.Task[None]] = []
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
            tasks.append(self._pump_task)
        for state in list(self._stream.values()):
            if state.edit_task is not None and not state.edit_task.done():
                state.edit_task.cancel()
                tasks.append(state.edit_task)
        tasks.extend(task for task in self._turn_tasks if not task.done())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._bus.unsubscribe(_SINK)

    def allowlist(self) -> tuple[list[str], list[str]]:
        """R-6 allowlist at call time: ``DISCORD_ALLOWED_USERS``/``_ROLES``
        (comma- or space-separated, stripped, empties dropped) win when
        set-and-nonempty, else the config lists; BOTH empty -> allow-all
        (documented PoC single-user default)."""

        def _split(env_name: str) -> list[str] | None:
            raw = os.environ.get(env_name)
            if raw is None or not raw.strip():
                return None
            return [
                token
                for token in (part.strip() for part in raw.replace(",", " ").split())
                if token
            ]

        users = _split("DISCORD_ALLOWED_USERS")
        roles = _split("DISCORD_ALLOWED_ROLES")
        allow = self._cfg.discord.allowlist
        return (
            users if users is not None else [str(item) for item in allow.allow_users],
            roles if roles is not None else [str(item) for item in allow.allow_roles],
        )

    def is_allowed(self, message: object) -> bool:
        """R-6 semantics: other bots always denied; user-id check always runs;
        roles only when a real member object is present (DM -> honest INFO
        note + denial unless the user id matched)."""
        author = getattr(message, "author", None)
        if author is not None and getattr(author, "bot", False):
            return False
        users, roles = self.allowlist()
        if not users and not roles:
            return True
        if str(getattr(author, "id", "")) in users:
            return True
        member = getattr(message, "member", None)
        if member is not None:
            return any(str(role.id) in roles for role in member.roles)
        if roles:
            self._log.info(
                "role allowlist configured but no member object (DM) — user-id allowlist only"
            )
        return False

    # ------------------------------------------------------------------
    # Inbound path (parity with gateway/app.py chat route)
    # ------------------------------------------------------------------
    async def on_message(self, message: object) -> None:
        """One inbound Discord message: ignore-list, allowlist, commands, chat."""
        text = str(getattr(message, "content", "") or "")
        if getattr(message, "author", None) is not None and getattr(message.author, "bot", False):
            return
        identity = self._client_user_id()
        if identity is not None and str(getattr(message.author, "id", "")) == str(identity):
            return
        if not text.strip():
            return
        if not self.is_allowed(message):
            self._log.debug(
                "discord message from %s denied by allowlist", getattr(message.author, "id", "?")
            )
            return
        channel = message.channel
        if text.strip() == "/new":
            await self._handle_new(message)
            return
        if text.strip() == "/status":
            await self._handle_status(message)
            return
        match = GATE_ANSWER_PATTERN.match(text.strip())
        if match is not None:
            await self._bus.publish(
                "gate.answered",
                {
                    "task_id": f"T{match.group(1)}",
                    "answer": match.group(2),
                    "channel": str(getattr(channel, "id", "")),
                },
            )
            await self._send(channel, f"✓ answer recorded for T{match.group(1)}")
            return
        await self._handle_chat(message)

    def _client_user_id(self) -> int | str | None:
        identity = getattr(self._client, "user", None)
        return getattr(identity, "id", None) if identity is not None else None

    async def _send(self, channel: object, text: str) -> object | None:
        try:
            return await self._client.send_message(channel, text)
        except Exception as exc:  # noqa: BLE001 — F8: Discord drops never crash the loop
            self._log.warning("discord send failed (%s): %s", getattr(channel, "id", "?"), exc)
            return None

    async def _handle_chat(self, message: object) -> None:
        text = str(getattr(message, "content", "") or "")
        channel = message.channel
        try:
            session = await self._manager.get_or_create(
                channel_handle=f"discord:{channel.id}"
            )
        except ValueError as exc:
            self._log.warning("discord chat: session resolve failed: %s", exc)
            return
        if session.state not in _ALLOWED_STATES:
            await self._send(
                channel, f"session busy ({session.state}) — /new to start a fresh conversation"
            )
            return
        fresh = self._transcript_length(session) == 0
        await session.append_event("user", {"text": text, "source": "discord"})
        self._session_channels[session.id] = channel.id
        if session.state != "routing":
            await self._manager.move(session, "routing")
        pre_len = self._transcript_length(session)
        route = await self._brain.route_async(text, session=session, session_manager=self._manager)
        if self._just_fell_back(session, pre_len):
            await self._bus.publish(
                "reply.chunk",
                {
                    "session_id": session.id,
                    "seq": None,
                    "text": "routed by fallback rules",
                    "source": "system",
                },
            )
        try:
            adapter = self._resolve_adapter(route.agent)
        except Exception as exc:  # noqa: BLE001 — resolve failures are honest 503-parity
            await self._bus.publish(
                "reply.final",
                {
                    "session_id": session.id,
                    "text": f"[run failed] no adapter wired for {route.agent} ({exc})",
                    "meta": {"failed": True},
                },
            )
            return
        route_line = f"[routed to {route.agent}] {route.task_title}"
        await self._bus.publish(
            "reply.chunk",
            {"session_id": session.id, "seq": 0, "text": route_line, "source": "router"},
        )
        await session.append_event("router", {"agent": route.agent, "task_title": route.task_title})
        if (fresh or session.title is None) and route.task_title:
            session.rename(route.task_title[:_TITLE_MAX])
        await self._manager.move(session, "agent_running")
        spec = RunSpec(
            prompt=self._assemble_prompt(session, route, text),
            profile=None,
            workdir=None,
            resume_ref=session.agents_refs.get(route.agent),
            timeout_s=getattr(self._cfg.agents, route.agent).timeout_s,
        )
        task = asyncio.get_running_loop().create_task(self._run_agent(session, route, spec, adapter))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    def _resolve_adapter(self, agent: str) -> object:
        """Gateway adapter seam: cached per agent; mapping/factory/registry."""
        cached = self._adapter_cache.get(agent)
        if cached is not None:
            return cached
        if self._adapters is None:
            adapter = registry_get(agent, self._cfg)
        elif callable(self._adapters):
            adapter = self._adapters(agent)
        else:
            entry = self._adapters.get(agent)
            if entry is None:
                raise KeyError(f"no adapter wired for {agent!r}")
            adapter = entry(agent) if callable(entry) and not hasattr(entry, "run") else entry
        self._adapter_cache[agent] = adapter
        return adapter

    async def _run_agent(
        self, session: Session, route: Route, spec: RunSpec, adapter: object
    ) -> None:
        """Mirror of the gateway relay loop (chunk/tool/final/failed + FSM),
        plus the R7 deviation: session_ref recording on terminal events."""
        started = time.perf_counter()
        seq = 0

        async def _chunk(event_text: str) -> None:
            nonlocal seq
            seq += 1
            await self._bus.publish(
                "reply.chunk",
                {"session_id": session.id, "seq": seq, "text": event_text, "source": "agent"},
            )
            await session.append_event(
                "agent_chunk", {"seq": seq, "text": event_text, "source": "agent"}
            )

        async def _final(final_text: str, *, failed: bool) -> None:
            meta: dict = {
                "agent": route.agent,
                "cost": None,
                "duration_s": round(time.perf_counter() - started, 3),
            }
            if failed:
                meta["failed"] = True
            await self._bus.publish(
                "reply.final", {"session_id": session.id, "text": final_text, "meta": meta}
            )
            await session.append_event("agent_final", {"text": final_text, "meta": meta})

        terminal_seen = False
        try:
            async for event in adapter.run(spec):  # type: ignore[attr-defined]
                if event.kind == "chunk":
                    await _chunk(event.text or "")
                elif event.kind == "tool":
                    brief = event.tool_brief or event.tool_name or "?"
                    await _chunk(f"[tool] {brief}")
                elif event.kind == "final":
                    if event.session_ref:
                        await session.set_agent_ref(route.agent, event.session_ref)
                    await _final(event.final_text or "", failed=False)
                    terminal_seen = True
                    break
                elif event.kind == "failed":
                    if event.session_ref:
                        await session.set_agent_ref(route.agent, event.session_ref)
                    await _final(f"[run failed] {event.error or 'unknown error'}", failed=True)
                    terminal_seen = True
                    break
                elif event.kind in ("started", "system"):
                    continue
            if not terminal_seen:
                await _final("[run failed] adapter stream ended without a final event", failed=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — honest terminal, never crash
            self._log.exception("agent run failed for session %s", session.id)
            await _final(f"[run failed] internal error: {exc}", failed=True)
        finally:
            try:
                await self._manager.move(session, "relaying")
                await self._manager.move(session, "idle")
            except SessionFSMError as exc:
                self._log.warning("session %s post-run moves skipped: %s", session.id, exc)

    def _assemble_prompt(self, session: Session, route: Route, text: str) -> str:
        """Gateway-parity prompt build (deliberate duplication — see module
        docstring): digests when ``route.need_context``, skill bodies for
        ``route.skill_hints``, ``## Input`` always last; enrichment never
        blocks a run."""
        del session
        sections: list[str] = []
        if route.need_context:
            try:
                memory_engine = MemoryEngine(self._cfg, self._bus)
                digests = [memory_engine.digest(name) for name in ("MEMORY", "USER", "lessons")]
                memory = "\n".join(digest for digest in digests if digest)
                if memory:
                    sections.append(f"### Memory digest\n{memory}")
            except Exception as exc:  # noqa: BLE001 — enrichment must never block a run
                self._log.warning("discord prompt: memory digests unavailable: %s", exc)
        if route.skill_hints:
            try:
                skill_engine = SkillsEngine(self._cfg)
                bodies = []
                for name in route.skill_hints:
                    body = skill_engine.get_skill_body(name)
                    if body:
                        bodies.append(f"#### skill: {name}\n{body}")
                if bodies:
                    sections.append("### Skills\n" + "\n\n".join(bodies))
            except Exception as exc:  # noqa: BLE001
                self._log.warning("discord prompt: skill bodies unavailable: %s", exc)
        sections.append(f"## Input\n{text}")
        return "\n\n".join(sections)

    def _transcript_length(self, session: Session) -> int:
        try:
            return len(read_jsonl(session.transcript_path))
        except (OSError, ValueError):
            return 0

    def _just_fell_back(self, session: Session, pre_len: int) -> bool:
        """m4b scan: the R5 fallback banner note appearing after ``pre_len``."""
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

    def _channel_for(self, session_id: str) -> int | None:
        """Discord channel id for a session: the live map first, else the
        session's ``discord:<id>`` meta channel (non-discord channels -> None)."""
        mapped = self._session_channels.get(session_id)
        if mapped is not None:
            return mapped
        try:
            meta_path = self._manager.sessions_root / session_id / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict):
            return None
        channel = meta.get("channel")
        if isinstance(channel, str) and channel.startswith("discord:"):
            try:
                return int(channel[len("discord:") :])
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------
    # /new and /status (m4d)
    # ------------------------------------------------------------------
    async def _handle_new(self, message: object) -> None:
        """/new: refuse unless idle; archive the old session, bind the new one
        to a fresh thread (or the same channel when threads are unavailable),
        note the link and acknowledge."""
        channel = message.channel
        handle = f"discord:{channel.id}"
        try:
            existing = await self._manager.get_or_create(channel_handle=handle)
        except ValueError as exc:
            self._log.warning("/new: session resolve failed: %s", exc)
            return
        if self._transcript_length(existing) == 0:
            await self._send(channel, "🆕 conversation started")
            return
        if existing.state != "idle":
            refusal = (
                "session failed — /new after it settles or continue chatting to retry"
                if existing.state == "failed"
                else f"busy ({existing.state}) — try again when the run finishes"
            )
            await self._send(channel, refusal)
            return
        try:
            existing.archive()  # sync idle -> archived; appends the state_change line
        except SessionFSMError as exc:
            self._log.warning("/new: archive refused for %s: %s", existing.id, exc)
            return
        self._log.info("/new archived session %s for channel %s", existing.id, handle)
        short = datetime.now(UTC).strftime("%H:%M")
        thread = None
        try:
            thread = await self._client.create_thread(channel, f"New chat {short}")
        except Exception as exc:  # noqa: BLE001 — thread failure falls back to the channel
            self._log.warning("thread creation failed (falling back): %s", exc)
            thread = None
        target = thread if thread is not None else channel
        fresh = await self._manager.get_or_create(channel_handle=f"discord:{target.id}")
        await fresh.append_event(
            "note", {"note": "new conversation started", "prior": existing.id}
        )
        self._session_channels[fresh.id] = target.id
        await self._send(target, "🆕 conversation started")

    async def _handle_status(self, message: object) -> None:
        """/status: compact disk-built status (NO R9/R11 dependencies) ≤1900 chars."""
        try:
            report = self._build_status()
        except Exception as exc:  # noqa: BLE001 — honest error, never crash
            await self._send(message.channel, f"status unavailable: {exc}")
            return
        await self._send(message.channel, report[:_STATUS_MAX])

    def _build_status(self) -> str:
        data_dir = Path(self._cfg.storage.data_dir)
        total = busy = idle_count = failed = 0
        root = self._manager.sessions_root
        if root.is_dir():
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    continue
                try:
                    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(meta, dict):
                    continue
                total += 1
                state = str(meta.get("state") or "")
                if state == "idle":
                    idle_count += 1
                elif state in _FAILED_STATES:
                    failed += 1
                elif state in _BUSY_STATES:
                    busy += 1
        inbox = 0
        active = 0
        tasks_root = data_dir / "tasks"
        inbox_root = tasks_root / "inbox"
        if inbox_root.is_dir():
            inbox = sum(1 for entry in inbox_root.iterdir() if entry.is_dir())
        if tasks_root.is_dir():
            for directory in sorted(tasks_root.iterdir()):
                if not directory.is_dir():
                    continue
                state_path = directory / "state.json"
                try:
                    parsed = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                state = str(parsed.get("state") or "")
                if state and state not in _TERMINAL_TASK_STATES:
                    active += 1
        scheduled = 0
        next_job = "—"
        scheduler_path = data_dir / "state" / "scheduler.json"
        if scheduler_path.is_file():
            try:
                parsed = json.loads(scheduler_path.read_text(encoding="utf-8"))
                jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
                rows = [row for row in jobs if isinstance(row, dict)] if isinstance(jobs, list) else []
                scheduled = len(rows)
                next_job = next(
                    (
                        str(row.get("id") or row.get("job_id"))
                        for row in sorted(rows, key=lambda row: str(row.get("at") or ""))
                        if str(row.get("id") or row.get("job_id") or "").strip()
                    ),
                    "—",
                )
            except (OSError, ValueError):
                return (
                    f"harness status — sessions: {total} (busy: {busy}, idle: {idle_count}, "
                    f"failed: {failed}) · tasks: inbox {inbox}, active {active} · "
                    "scheduled jobs: 0 (no scheduler state)"
                )
        return (
            f"harness status — sessions: {total} (busy: {busy}, idle: {idle_count}, "
            f"failed: {failed}) · tasks: inbox {inbox}, active {active} · "
            f"scheduled jobs: {scheduled} (next: {next_job})"
        )

    # ------------------------------------------------------------------
    # Outbound pump (reply sink) — the bot's ONLY bus subscription
    # ------------------------------------------------------------------
    async def _pump(self) -> None:
        """Drain the ``discord-bot`` sink forever; failures log and continue."""
        queue = self._bus_queue
        assert queue is not None, "pump started before start()"
        while True:
            topic, payload = await queue.get()
            try:
                if topic == "reply.chunk":
                    sid = str(payload.get("session_id") or "")
                    if sid:
                        await self.handle_chunk(sid, str(payload.get("text") or ""))
                elif topic == "reply.final":
                    sid = str(payload.get("session_id") or "")
                    failed = bool((payload.get("meta") or {}).get("failed"))
                    if sid:
                        await self.handle_final(
                            sid, str(payload.get("text") or ""), failed=failed
                        )
                elif topic in ("gate.opened", "gate.notify"):
                    await self._notice(f"⏸ task {payload.get('task_id')}: {payload.get('question')}")
                elif topic == "task.state":
                    await self._notice(
                        f"↻ task {payload.get('task_id')}: "
                        f"{payload.get('old')} → {payload.get('new')}"
                    )
                # gate.answered, session.status, user.msg, system.log ... ignored
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never crash the pump
                self._log.exception("discord pump error")

    async def handle_chunk(self, session_id: str, text: str) -> None:
        """Buffer one stream chunk and (coalesced) edit the message in place."""
        channel = self._channel_for(session_id)
        if channel is None:
            return  # a web-only session
        state = self._stream.setdefault(session_id, _StreamState())
        state.buffer += text
        now = self._clock()
        if state.edit_task is None:
            delay = max(0.0, state.next_edit_at - now)
            state.edit_task = asyncio.get_running_loop().create_task(
                self._edit_later(session_id, channel, delay)
            )

    async def _edit_later(self, session_id: str, channel_id: int, delay: float) -> None:
        """One coalesced edit round: sleep, resolve the channel, send-or-edit."""
        await asyncio.sleep(delay)
        state = self._stream.get(session_id)
        if state is None or self._stopping:
            return
        channel = await self._resolve_channel(channel_id)
        if channel is None:
            self._log.warning("discord stream channel %s vanished for %s", channel_id, session_id)
            self._stream.pop(session_id, None)
            return
        if state.message is None:
            try:
                state.message = await self._client.send_message(channel, state.buffer)
            except Exception as exc:  # noqa: BLE001 — F8: the web stays unaffected
                self._log.warning("discord stream send failed for %s: %s", session_id, exc)
                self._stream.pop(session_id, None)
                return
        else:
            try:
                await self._client.edit_message(state.message, state.buffer)
            except Exception as exc:  # noqa: BLE001 — F8
                self._log.warning("discord stream edit failed for %s: %s", session_id, exc)
                self._stream.pop(session_id, None)
                return
        state.next_edit_at = self._clock() + (self._cfg.discord.chunk_ms / 1000)
        state.edit_task = None

    async def _resolve_channel(self, channel_id: int) -> object | None:
        """get_channel-first, fetch-as-fallback; failures -> None (2.8)."""
        channel = self._client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(channel_id)
            except Exception as exc:  # noqa: BLE001 — failures -> None
                self._log.debug("fetch_channel(%s) failed: %s", channel_id, exc)
                return None
        return channel

    async def handle_final(self, session_id: str, text: str, *, failed: bool) -> None:
        """Terminal reply: the streamed message is edited to the first slice,
        the remainder (if any) is sent sequentially. Failures are logged only."""
        del failed  # failed finals already carry the honest "[run failed] ..." text
        channel = self._channel_for(session_id)
        if channel is None:
            self._stream.pop(session_id, None)
            return
        state = self._stream.pop(session_id, None)
        if state is not None and state.edit_task is not None and not state.edit_task.done():
            state.edit_task.cancel()
            try:
                await state.edit_task
            except asyncio.CancelledError:
                pass
        chars = getattr(self._cfg.discord, "chunk_chars", _ROUTE_CHUNK_CHARS) or _ROUTE_CHUNK_CHARS
        slices = [text[index : index + chars] for index in range(0, len(text), chars)] or [""]
        if state is not None and state.message is not None:
            first = slices[0] or "(failed)"
            try:
                await self._client.edit_message(state.message, first)
            except Exception as exc:  # noqa: BLE001 — F8
                self._log.warning("discord final edit failed for %s: %s", session_id, exc)
                return
            slices = slices[1:]
        for piece in slices:
            channel_obj = await self._resolve_channel(channel)
            if channel_obj is None:
                self._log.warning("discord final channel %s vanished for %s", channel, session_id)
                return
            await self._send(channel_obj, piece)

    async def _notice(self, text: str) -> None:
        """Q4 home-channel path for notices with NO originating thread."""
        home = self._home_channel()
        if home is None:
            self._log_notice_drop(text)
            return
        try:
            channel_id = int(str(home))
        except (TypeError, ValueError):
            self._log_notice_drop(text)
            return
        channel = self._client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(channel_id)
            except Exception as exc:  # noqa: BLE001 — failures -> None (dropped)
                self._log.debug("fetch_channel(%s) failed: %s", channel_id, exc)
                channel = None
        if channel is None:
            self._log_notice_drop(text)
            return
        await self._send(channel, text)

    def _log_notice_drop(self, text: str) -> None:
        """Rate-limited (60s) honest drop log when no home channel exists."""
        now = self._clock()
        if now - self._last_notice_drop >= _NOTICE_DROP_LOG_INTERVAL_S:
            self._last_notice_drop = now
            self._log.info("notice dropped (no home channel configured): %s", text)

    def _home_channel(self) -> str | None:
        """Q4 resolution: config binding first, then the env scan, else None."""
        configured = self._cfg.discord.home_channel_id
        if configured:
            return str(configured)
        for name in HOME_CHANNEL_ENVS:
            value = os.environ.get(name)
            if value is not None and value.strip():
                return value.strip()
        return None

    # ------------------------------------------------------------------
    # spawn (real client wiring; heavy import lives here only)
    # ------------------------------------------------------------------

async def spawn_bot(
    cfg: HarnessConfig,
    bus: Bus,
    manager: SessionManager,
    *,
    client_factory: Callable[[], object] | None = None,
    log: logging.Logger | None = None,
) -> DiscordBot | None:
    """Build + start the Discord bot on the RUNNING loop; ``None`` (reason
    logged at INFO "discord bot disabled: ...") when disabled at config level
    or when ``DISCORD_BOT_TOKEN`` is unset/ empty. With ``client_factory`` the
    product is used as the shim client verbatim (test/smoke seam — the real
    ``import discord`` is skipped); otherwise the real discord.py client is
    built here (the ONLY lazy ``import discord`` in the module — module import
    stays lightweight and testable). First-connect failures log ERROR + return
    None (F8: web-only; the PoC does not auto-reconnect — supervisord restarts
    the whole process). The token is NEVER logged."""
    _logger = log if log is not None else _LOG
    if not cfg.discord.enabled:
        _logger.info("discord bot disabled: discord.enabled false")
        return None
    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        _logger.info("discord bot disabled: DISCORD_BOT_TOKEN unset")
        return None
    if client_factory is not None:
        bot = DiscordBot(cfg, bus, manager, client=client_factory(), log=_logger)
        await bot.start()
        return bot
    import discord  # lazy: keep module import lightweight + testable (spec rule)

    intents = discord.Intents.default()
    intents.members = True  # privileged; the R-6 role path tolerates its absence
    intents.message_content = True  # REQUIRED (DISCORD_SETUP docs) for on_message text
    inner = discord.Client(intents=intents)
    client = _RealClient(inner)
    bot = DiscordBot(cfg, bus, manager, client=client, log=_logger)

    first_connect: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    @inner.event
    async def on_ready() -> None:
        if inner.user is not None:
            _logger.info("discord bot connected as %s", inner.user)
        if not first_connect.done():
            first_connect.set_result(None)

    @inner.event
    async def on_message(message: object) -> None:
        await bot.on_message(message)

    async def _runner() -> None:
        try:
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — surfaced through first_connect below
            return
        finally:
            if not first_connect.done():
                first_connect.set_exception(
                    ConnectionError("discord client exited before on_ready")
                )

    task = asyncio.get_running_loop().create_task(_runner())

    def _runner_done(done: asyncio.Task[None]) -> None:
        if first_connect.done():
            return
        exc = None if done.cancelled() else done.exception()
        first_connect.set_exception(exc or ConnectionError("discord client stopped"))

    task.add_done_callback(_runner_done)
    await bot.start()
    try:
        await asyncio.wait_for(asyncio.shield(first_connect), timeout=_FIRST_CONNECT_GRACE_S)
    except TimeoutError:
        _logger.info("discord gateway connect still pending; continuing web-first")
        first_connect.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
    except Exception as exc:  # noqa: BLE001 — first-connect failure is honest
        _logger.error("discord bot disabled: connect failed: %s", exc)
        return None
    return bot


class _RealClient:
    """Six-method shim wrapping a real :class:`discord.Client` (design 2.8).

    The whole client surface the bot logic may touch; each method maps one
    line onto discord.py's API (``channel.send`` / ``message.edit`` /
    ``Client.get_channel`` / ``Client.fetch_channel`` / ``create_thread``).
    Failures inside ``fetch_channel`` degrade to ``None`` per the surface
    contract; send/edit failures flow to the caller (the bot logs + drops).
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner

    @property
    def user(self) -> object | None:
        return getattr(self._inner, "user", None)

    async def send_message(self, channel: object, text: str) -> object | None:
        return await channel.send(text)  # type: ignore[attr-defined]

    async def edit_message(self, message: object, text: str) -> object | None:
        return await message.edit(content=text)  # type: ignore[attr-defined]

    def get_channel(self, channel_id: int) -> object | None:
        return self._inner.get_channel(channel_id)  # type: ignore[attr-defined]

    async def fetch_channel(self, channel_id: int) -> object | None:
        try:
            return await self._inner.fetch_channel(channel_id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — surface contract: failures -> None
            return None

    async def create_thread(self, channel: object, name: str) -> object | None:
        factory = getattr(channel, "create_thread", None)
        if factory is None:  # DMs / unsupported channel types: surface says None
            return None
        try:
            return await factory(name=name)
        except Exception:  # noqa: BLE001 — no-perms: fall back to the plain channel
            return None

    async def start(self, token: str) -> None:
        await self._inner.start(token)  # type: ignore[attr-defined]
