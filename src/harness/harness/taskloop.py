"""Task-folder loop: watcher + single runner + SUBTASK_DROP chaining + replay.

Implements the task loop of ARCHITECTURE.md rev2 sections 2.9 (components,
invariants), 3.4 (boot replay F17) and 3.5 (the authoritative task flow) per
DECISIONS.md D14 (the inbox directory IS the queue), D16 (``analyze()`` —
deterministic fallback is internal to the router; any exception from the
injected router is provider-down -> honest FAILED, M-8b) and D17 (fenced
``SUBTASK_DROP`` blocks create inbox tasks, never inline runs — the
harness-as-sole-invoker invariant S1.5: THIS runner is the ONLY caller of
``adapter.run`` for task-origin runs, row 23).

Structure:

- :class:`TaskLoop` — wires a :class:`harness.tasks.TaskStore` (schema/FSM
  primitives of WP1), the session manager (a task's transcript IS a session;
  task sessions carry the ``T<task_id>:`` title), the optional bus, the
  adapter seam and the router seam into one coroutine family.
- **Watcher** — poll beat over ``tasks/inbox`` (dedupe key = task id, F13:
  ``task.appeared`` published ONCE per id; picked-up ids never re-fire even
  when re-dropped). Pickup is the single ``move_from_inbox`` move + ``born``
  state.json + ready-queue enqueue. The watcher never runs a task.
- **Single runner** — ONE consumer loop over an in-memory
  :class:`asyncio.Queue`, concurrency-capped by ``tasks.max_concurrent``
  (default 1 => literally one by one). Per task the §3.5 flow: validate ->
  deps ready (no -> WAITING_DEPS, re-checked here on every own-task DONE) ->
  analyze (D16, via the router seam) -> READY -> RUNNING (attempt+=1) ->
  session (origin=task) -> adapter stream -> SUBTASK_DROP handling ->
  result.md -> DONE. Run failures count attempts (M-8: retry while
  ``attempt <= tasks.retries``, non-agent and agent errors alike) then a
  FAILED with the honest reason. A gate question (``analysis.mode == gate``
  and not task-oneway) parks the task in WAITING_INPUT — persistence here,
  answer()/resume with the SAME session + refs. Gate engine (R10, D18/F15):
  one persisted row per gate event in ``state/gates.jsonl``, ONE 24 h
  ``tasks.gate_timeout_s`` asyncio timer per gate (parked task, in-process),
  exactly one ``gate.notify`` re-notify on expiry then FAILED(gate_timeout);
  ``answer()`` raises :class:`~harness.tasks.TaskSchemaError` (never
  ``ValueError``) and the loop's own bus sink consumes ``gate.answered``
  (the Discord path) the same way the HTTP path does; ``replay()`` restores
  live gates and ``start()`` re-arms their timers with a fresh full timeout.
- **replay()** (F17, §3.4) — sync boot walk over ``tasks/*/state.json``:
  live-session tasks (session transcript still ``agent_running``/``relaying``)
  are ``FAILED(restarted)``, never double-run; others are interrupted ->
  re-queued EXACTLY ONCE per process (in-memory guard; done-markers and
  terminal states are honored idempotently). WAITING_INPUT tasks get a live
  in-memory gate record restored (R10; timers are armed by ``start()`` with
  the FULL ``tasks.gate_timeout_s`` since the old asyncio timer is gone).
  Counters:
  ``{"tasks", "requeued", "failed_restarted", "gate_restored", "deps_waiting"}``.

Blocking note (PoC): ``RouterBrain.analyze`` is synchronous and is called
directly from the runner coroutine (block-the-loop accepted, mirroring the
gateway); a test double may hand back an awaitable, which is awaited.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from harness.adapters import get as registry_get
from harness.adapters.base import RunSpec
from harness.blocks import parse_blocks, strip_blocks
from harness.config import HarnessConfig
from harness.errors import SessionFSMError
from harness.memory import MemoryEngine
from harness.persist import AtomicCounter, JsonlWriter, read_jsonl
from harness.router import RouterBrain
from harness.session import Session
from harness.session_manager import SessionManager
from harness.skills import SkillsEngine
from harness.tasks import (
    TRANSITIONS,
    TaskDir,
    TaskExistsError,
    TaskSchemaError,
    TaskStore,
    load_task_yaml,
    subtask_depth_cap,
    validate_subtask_drop,
)

__all__ = ["TaskLoop"]

_LOG = logging.getLogger("harness.taskloop")

_STATE_FILENAME = "state.json"
_TASK_YAML_FILENAME = "task.yaml"

# The global runs ledger the gateway /materials report summarizes (5.3/7.2):
# one row per runner adapter run, {ts, task_id, agent, attempt, state}.
_RUNS_LEDGER_FILENAME = "runs.jsonl"

# The persisted gate audit trail (R10/D18): one row per gate event, NEVER read
# back for runtime logic (boot re-arm restores from state.json + events.jsonl).
_GATES_LEDGER_FILENAME = "gates.jsonl"

# The harness-owned workdir root for drop-block workdir fields (task.yaml rule).
_WORKDIR_PREFIX = "/app/projects/"

# Boot-replay: session states that mean "the agent child may have survived" (F6).
_LIVE_SESSION_STATES = frozenset({"agent_running", "relaying"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class TaskLoop:
    """Watch inbox -> pick up -> analyze -> run -> chain tasks; one consumer.

    Construct with the harness config, the session manager, an optional bus
    (duck-typed ``async def publish(topic, payload)``), an adapter seam and a
    router seam:

    - ``adapters``: ``None`` -> the :mod:`harness.adapters` registry
      (``get(agent, cfg)``); a mapping agent-name -> adapter (or factory);
    - ``adapter_factory``: callable ``agent -> adapter`` (wins over the
      mapping when set);
    - ``router``: ``None`` -> a :class:`~harness.router.RouterBrain` built
      lazily on the first analyze; tests inject an object with
      ````.analyze(task_desc, *, workdir=...)`` returning an
      :class:`~harness.router.Analysis` (or an awaitable of one);
    - ``scheduler_sink``: optional async callable(dict) invoked when a
      SUBTASK_DROP block carries a ``schedule:`` demand (WP4 seam). Duck-typed
      on purpose — NO ``harness.scheduler`` import, no hard dependency; the
      supervisor wiring owns the late-bound engine handoff (R11 item 9).

    Resolved adapters are cached per agent — this cache is the seam the row-23
    spy wraps: every task-origin ``adapter.run`` flows through
    :meth:`TaskLoop._resolve_adapter` from the single runner.
    """

    def __init__(
        self,
        cfg: HarnessConfig,
        manager: SessionManager,
        bus: Any | None = None,
        adapters: Any | None = None,
        router: Any | None = None,
        adapter_factory: Any | None = None,
        scheduler_sink: Any | None = None,
    ) -> None:
        self._cfg = cfg
        self._manager = manager
        self._bus = bus
        self._adapters = adapters
        self._adapter_factory = adapter_factory
        self._scheduler_sink = scheduler_sink
        self._injected_router = router
        self._router_obj: Any | None = None
        self._store = TaskStore(cfg, bus=bus)
        self._adapter_cache: dict[str, Any] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._slots = asyncio.Semaphore(max(1, int(cfg.tasks.max_concurrent)))
        self._workers: set[asyncio.Task] = set()
        self._watcher_task: asyncio.Task | None = None
        self._runner_task: asyncio.Task | None = None
        self._subscriber_task: asyncio.Task | None = None
        self._boot_task: asyncio.Task | None = None
        self._gate_rearm_task: asyncio.Task | None = None
        self._sink_name: str | None = None
        self._sink_queue: asyncio.Queue | None = None
        self._running = False
        # F13 dedupe: ids whose task.appeared was published once (first sighting)
        # and ids ever picked up (never re-fired, even if re-dropped into the
        # inbox — the re-dropped dir is left in place, documented deviation).
        self._announced: set[str] = set()
        self._picked: set[str] = set()
        # Boot replay state: requeue-once guards + the pre-loop pending list.
        self._boot_queued: set[str] = set()
        self._boot_gates: set[str] = set()
        self._boot_pending: list[str] = []
        # Gate park (WAITING_INPUT) in-process bookkeeping for answer()/resume
        # + the 24 h F15 timers: each record is
        # {prompt, analysis, session?, session_id?, agent?, task_oneway,
        #  question, attempt, deadline_ts, renotified, timer}.
        self._gates: dict[str, dict] = {}
        # Per-gate asyncio locks (created with the gate/lazily): serialize
        # answer() against _gate_timeout() so an answer that popped the record
        # can never be followed by a spurious timeout re-notify/publish.
        self._gate_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public control surface
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the watcher + runner (and sink consumer when the bus allows
        subscriptions); drains boot-replay requeues once the loop is live."""
        if self._running:
            return
        self._running = True
        loop = asyncio.get_running_loop()
        self._watcher_task = loop.create_task(self._watch(), name="taskloop-watcher")
        self._runner_task = loop.create_task(self._runner(), name="taskloop-runner")
        if self._bus is not None and callable(getattr(self._bus, "subscribe", None)):
            try:
                self._sink_queue = self._bus.subscribe("taskloop")
                self._sink_name = "taskloop"
            except ValueError:
                self._sink_name = None
                self._sink_queue = None
        if self._sink_name is not None:
            self._subscriber_task = loop.create_task(
                self._subscriber_loop(), name="taskloop-subscriber"
            )
        if self._boot_pending:
            self._boot_task = loop.create_task(self._boot_drain(), name="taskloop-boot-drain")
        if self._gates:
            # R10 boot re-arm: restored WAITING_INPUT gates get their F15
            # timers armed here (async context), with a FRESH full timeout.
            self._gate_rearm_task = loop.create_task(
                self._arm_restored_gates(), name="taskloop-gate-rearm"
            )

    async def stop(self) -> None:
        """Cancel watcher/runner/subscriber/boot (+ in-flight workers and ALL
        pending gate timers) cleanly: every cancelled task is awaited, no
        pending-task warnings. Idempotent."""
        self._running = False
        gate_timers = [
            record.get("timer")
            for record in self._gates.values()
            if isinstance(record.get("timer"), asyncio.Task)
        ]
        tasks = [
            task
            for task in (
                self._watcher_task,
                self._runner_task,
                self._subscriber_task,
                self._boot_task,
                self._gate_rearm_task,
                *gate_timers,
            )
            if task is not None
        ]
        workers = list(self._workers)
        for task in (*tasks, *workers):
            task.cancel()
        if tasks or workers:
            await asyncio.gather(*tasks, *workers, return_exceptions=True)
        if self._sink_name is not None and self._bus is not None:
            try:
                self._bus.unsubscribe(self._sink_name)
            except Exception as exc:  # noqa: BLE001 — stop must always finish
                _LOG.warning("unsubscribe during stop failed: %s", exc)
        self._sink_name = None
        self._sink_queue = None
        self._watcher_task = self._runner_task = self._subscriber_task = None
        self._boot_task = self._gate_rearm_task = None
        for record in self._gates.values():
            record["timer"] = None

    async def answer(self, task_id: str, text: str) -> None:
        """Answer a parked WAITING_INPUT gate (R10): RUNNING again, adapter
        resumed with the answer appended to the stored prompt, SAME session +
        refs. Raises :class:`~harness.tasks.TaskSchemaError` (mapped to
        409/404 by the gateway) when the task is not WAITING_INPUT or no live
        gate exists in this process (restart?).

        Restored gates (boot re-arm): the ORIGINAL agent prompt cannot be
        rebuilt verbatim after a restart — the stored prompt of the fresh-gate
        path dies with the process. The resume prompt is instead assembled in
        ``replay()`` from the persisted question + the session transcript's
        last user / agent-final messages, so the answering path code is
        IDENTICAL for fresh and restored gates (documented simplification:
        the exact pre-restart prompt context is approximated).
        """
        task = self._store.open_task(task_id)
        if task.state != "WAITING_INPUT":
            raise TaskSchemaError(
                task_id, f"task {task_id}: not WAITING_INPUT (state {task.state}); cannot answer"
            )
        async with self._gate_locks.setdefault(task_id, asyncio.Lock()):
            # Serialize vs _gate_timeout(): whoever gets the lock first wins
            # (answer resumes; timeout re-notifies + fails). The lock entry is
            # popped with the record — the gate is gone either way.
            live = self._gates.pop(task_id, None)
            self._gate_locks.pop(task_id, None)
        if live is None:
            raise TaskSchemaError(
                task_id,
                f"task {task_id}: gate state not live in this process "
                "(restart? gate timers + records re-arm at boot, R10)",
            )
        self._cancel_gate_timer(live)
        session: Session | None = live.get("session")
        if session is None:
            # Restored gate: re-open the persisted session (transcript intact).
            session_id = live.get("session_id")
            session = await self._manager.get_or_create(
                session_id if isinstance(session_id, str) and session_id.strip() else None,
                channel_handle=None,
            )
        agent = live.get("agent")
        if not isinstance(agent, str) or not agent.strip():
            agent = self._transcript_agent(session) or "hermes"
        await session.append_event("user", {"text": text})
        await task.append_event("gate_answered", {"answer": text})
        await task.transition("RUNNING")
        self._gate_row(task_id, "answered")
        prompt = f"{live['prompt']}\n\n## User answer\n{text}"
        await self._attempts_loop(
            task, session, live["analysis"], agent, prompt,
            task_oneway=bool(live.get("task_oneway")),
        )

    # ------------------------------------------------------------------
    # Gate engine (R10/D18/F15): rows, timers, re-notify, timeout fail
    # ------------------------------------------------------------------
    def _gate_row(
        self,
        task_id: str,
        event: str,
        *,
        deadline_ts: str | None = None,
        data: dict | None = None,
    ) -> None:
        """One gate event row in ``state/gates.jsonl`` (R10, like
        ``_ledger_row``): ``{ts, task_id, event, deadline_ts?, data?}``.
        The file is an audit trail — it is NEVER read back for runtime logic
        (boot re-arm restores from state.json + the task events.jsonl).
        Never blocks a park/answer/timeout on a ledger failure."""
        row: dict = {"ts": _utc_now(), "task_id": task_id, "event": event}
        if deadline_ts is not None:
            row["deadline_ts"] = deadline_ts
        if data is not None:
            row["data"] = dict(data)
        try:
            JsonlWriter(
                Path(self._cfg.storage.data_dir) / "state" / _GATES_LEDGER_FILENAME
            ).append(row)
        except (OSError, TypeError, ValueError) as exc:
            _LOG.warning("gates ledger append for task %s failed: %s", task_id, exc)

    def _gate_timeout_s(self) -> float:
        """The F15 timeout: ``tasks.gate_timeout_s`` (default 24 h = 86400)."""
        return max(0.0, float(getattr(self._cfg.tasks, "gate_timeout_s", 86400)))

    def _deadline_ts(self, timeout_s: float) -> str:
        return (
            datetime.now(UTC) + timedelta(seconds=timeout_s)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _arm_gate_timer(self, task_id: str, timeout_s: float) -> None:
        """Arm ONE background asyncio timer for this parked gate (no
        apscheduler): on expiry it fires :meth:`_gate_timeout` once. The task
        handle rides the gate record; ``stop()`` cancels every pending timer."""
        record = self._gates.get(task_id)
        if record is None or record.get("timer") is not None:
            return  # already armed
        self._gate_locks.setdefault(task_id, asyncio.Lock())
        record["renotified"] = False
        record["deadline_ts"] = self._deadline_ts(timeout_s)
        record["timer"] = asyncio.create_task(
            self._gate_after(task_id, timeout_s), name=f"taskloop-gate-{task_id}"
        )

    async def _gate_after(self, task_id: str, timeout_s: float) -> None:
        """Timer body: sleep the F15 window, then run the timeout sequence
        (cancellation during stop() is the normal exit)."""
        await asyncio.sleep(timeout_s)
        await self._gate_timeout(task_id)

    async def _gate_timeout(self, task_id: str) -> None:
        """F15: the gate expired unanswered. Exactly ONE re-notify (guarded by
        the in-memory ``renotified`` flag): publish ``gate.notify`` with
        ``kind: "timeout"`` so the/UI Discord re-ping, append a task note
        event, then the honest terminal — WAITING_INPUT -> FAILED
        (reason=gate_timeout) with a FAILED ledger row, and the ``timeout``
        row in gates.jsonl. A task that moved on in the meantime is left
        untouched (the stale gate record is dropped). The whole sequence is
        serialized against ``answer()`` with the per-gate lock, and the record
        is re-checked live right before the publish — an answer that popped
        the record can never be followed by a publish. On bookkeeping failure
        the terminal is still attempted best-effort (FAILED transition, gate
        drop, ``timeout`` row with the error): the task must never survive as
        an unwatched WAITING_INPUT."""
        # The per-gate lock exists whenever the gate record is live (created
        # lazily in _arm_gate_timer); a missing entry means the gate record is
        # already gone — the timeout is a no-op and must not resurrect a lock.
        async with (self._gate_locks.get(task_id) or asyncio.Lock()):
            record = self._gates.get(task_id)
            if record is None or record.get("renotified"):
                if record is not None:  # already handled: drop the stale record
                    self._drop_gate(task_id)
                return
            record["renotified"] = True
            try:
                task = self._store.open_task(task_id)
                if task.state != "WAITING_INPUT" or self._gates.get(task_id) is not record:
                    self._drop_gate(task_id)
                    return
                await self._publish(
                    "gate.notify",
                    {
                        "task_id": task_id,
                        "question": record.get("question"),
                        "kind": "timeout",
                    },
                )
                await task.append_event(
                    "note", {"note": "gate timeout: re-notify sent once (F15); failing task"}
                )
                self._ledger_row(
                    task_id, str(record.get("agent") or "?"), int(record.get("attempt", 0)),
                    "FAILED",
                )
                await task.transition("FAILED", reason="gate_timeout")
                self._gate_row(task_id, "timeout", deadline_ts=record.get("deadline_ts"))
                self._drop_gate(task_id)
            except Exception as exc:  # noqa: BLE001 — timeout bookkeeping must not crash the timer
                _LOG.warning("gate timeout handling for task %s failed: %s", task_id, exc)
                try:  # honest failure: still try the terminal, best-effort
                    task = self._store.open_task(task_id)
                    await task.transition("FAILED", reason="gate_timeout")
                except Exception as trans_exc:  # noqa: BLE001 — best-effort
                    _LOG.warning(
                        "gate timeout FAILED transition for task %s failed: %s",
                        task_id, trans_exc,
                    )
                self._gate_row(
                    task_id, "timeout", deadline_ts=record.get("deadline_ts"),
                    data={"error": str(exc)},
                )
                self._drop_gate(task_id)

    def _cancel_gate_timer(self, record: dict) -> None:
        timer = record.get("timer")
        record["timer"] = None
        if isinstance(timer, asyncio.Task) and not timer.done():
            timer.cancel()

    def _drop_gate(self, task_id: str) -> None:
        """Pop the gate record and cancel its (pending) timer."""
        record = self._gates.pop(task_id, None)
        if record is not None:
            self._cancel_gate_timer(record)
        self._gate_locks.pop(task_id, None)

    def _transcript_agent(self, session: Session) -> str | None:
        """The agent of the session's last ``agent_final`` record (the
        restored-gate agent recovery: state.json + task.yaml do not carry the
        runner agent). Best-effort: ``None`` on any read problem."""
        try:
            records = read_jsonl(session.transcript_path)
        except (OSError, ValueError):
            return None
        for record in reversed(records):
            if record.get("kind") == "agent_final":
                meta = record.get("meta")
                if isinstance(meta, dict) and isinstance(meta.get("agent"), str):
                    return str(meta["agent"])
        return None

    # ------------------------------------------------------------------
    # Boot replay (F17, §3.4) — sync, once at boot
    # ------------------------------------------------------------------
    def replay(self) -> dict[str, int]:
        """Recover the task world at boot: FAILED(restarted) for live-session
        tasks, INTERRUPTED -> READY re-queue once per process for the rest,
        WAITING_INPUT restored into a live in-memory gate record (R10 —
        question from the task ``gate_opened`` event, resume prompt + agent
        from the session transcript, session re-opened lazily by ``answer()``;
        the F15 timers arm in ``start()`` with a FRESH full timeout since the
        old asyncio timers are gone). Interrupted others are re-queued
        EXACTLY ONCE per process. Idempotent: a second call observes only
        DONE/FAILED/already-queued states and produces NO new re-queues."""
        counts: dict[str, int] = {
            "tasks": 0,
            "requeued": 0,
            "failed_restarted": 0,
            "gate_restored": 0,
            "deps_waiting": 0,
        }
        for task_id, state in sorted(self._store.list_tasks().items()):
            counts["tasks"] += 1
            if state in ("DONE", "FAILED"):
                continue
            if state == "WAITING_INPUT":
                # Checked BEFORE the live-session F6 branch: a gate park
                # deliberately leaves the session ``agent_running`` (stream
                # suspended in _stream_once) — that IS the parked gate, not
                # an interruptible child. Restore it (D18/F15: gates re-arm
                # at boot; the answer resumes the SAME session via --resume).
                if task_id not in self._boot_gates:
                    self._boot_gates.add(task_id)
                    self._restore_gate(task_id)
                    counts["gate_restored"] += 1
                continue
            state_data = self._store.read_state(task_id) or {}
            if self._agent_session_live(state_data.get("agent_session")):
                counts["failed_restarted"] += self._replay_failed_restarted(task_id, state)
                continue
            if (self._store.root / task_id / "result.md").is_file():
                self._raw_note(task_id, {"reason": "result.md present at boot; skipped"})
                continue
            if state == "WAITING_DEPS":
                counts["deps_waiting"] += 1
                try:
                    if self._store.ready_check(task_id) and self._raw_move(task_id, state, "READY"):
                        self._requeue_boot(task_id, counts)
                except TaskSchemaError as exc:
                    _LOG.warning("replay: deps re-check for %s failed: %s", task_id, exc)
            elif state in ("RUNNING", "ANALYZING"):
                self._raw_note(task_id, {"reason": "interrupted by restart"})
                if self._raw_move(task_id, state, "INTERRUPTED") and self._raw_move(
                    task_id, "INTERRUPTED", "READY"
                ):
                    self._requeue_boot(task_id, counts)
            elif state in ("READY", "appeared"):
                self._requeue_boot(task_id, counts)
            else:
                _LOG.warning("replay: task %s has unknown state %s; skipped", task_id, state)
        return counts

    def _replay_failed_restarted(self, task_id: str, state: str) -> int:
        """Live agent session at restart: FAILED(restarted), never double-run.

        FAILED is FSM-legal from RUNNING/ANALYZING (and WAITING_INPUT, but
        that state is handled BEFORE this branch — a parked gate's session is
        agent_running by design, so it restores instead); READY and
        WAITING_DEPS cannot legally reach FAILED, so they park as INTERRUPTED
        WITHOUT requeue (honest never-double-run; the next boot — with the
        child dead — requeues them via the READY path)."""
        self._raw_note(
            task_id,
            {"reason": "agent session live at restart; failed(restarted), never double-run"},
        )
        if state in ("RUNNING", "ANALYZING", "WAITING_INPUT"):
            if self._raw_move(task_id, state, "FAILED", reason="restarted"):
                return 1
            return 0
        if state in ("READY", "WAITING_DEPS"):
            self._raw_move(task_id, state, "INTERRUPTED")
            return 1
        return 0  # appeared with a session ref: impossible in practice; requeue

    def _requeue_boot(self, task_id: str, counts: dict[str, int]) -> None:
        if task_id in self._boot_queued:
            return
        self._boot_queued.add(task_id)
        self._boot_pending.append(task_id)
        counts["requeued"] += 1

    def _raw_move(self, task_id: str, old: str, new: str, reason: str | None = None) -> bool:
        """Raw replay-time FSM move (no per-task lock needed at boot, the
        session_manager.replay pattern): guard -> atomic state.json rewrite +
        one ``state_change`` event. No bus publish (boot time)."""
        if new not in TRANSITIONS.get(old, set()):
            _LOG.warning("replay: %s: illegal replay move %s -> %s; skipped", task_id, old, new)
            return False
        directory = self._store.root / task_id
        AtomicCounter(directory / _STATE_FILENAME).set({"state": new, "updated": _utc_now()})
        data: dict = {"old": old, "new": new}
        if reason is not None:
            data["reason"] = reason
        JsonlWriter(directory / "events.jsonl").append(
            {"ts": _utc_now(), "task_id": task_id, "kind": "state_change", "data": data}
        )
        return True

    def _raw_note(self, task_id: str, data: dict) -> None:
        directory = self._store.root / task_id
        try:
            JsonlWriter(directory / "events.jsonl").append(
                {"ts": _utc_now(), "task_id": task_id, "kind": "note", "data": dict(data)}
            )
        except (OSError, ValueError) as exc:
            _LOG.warning("replay: note for %s failed: %s", task_id, exc)

    def _agent_session_live(self, agent_session: Any) -> bool:
        """True iff the task's agent session transcript still ends in a
        mid-run state (F6 machinery): the child may have survived -> don't
        double-run. Torn-tail tolerant (read_jsonl drops a torn last line)."""
        if not isinstance(agent_session, str) or not agent_session.strip():
            return False
        transcript = self._manager.sessions_root / agent_session / "transcript.jsonl"
        if not transcript.is_file():
            return False
        try:
            records = read_jsonl(transcript)
        except (OSError, ValueError):
            return False
        for record in reversed(records):
            state = record.get("state")
            if isinstance(state, str):
                return state in _LIVE_SESSION_STATES
        return False

    def _restore_gate(self, task_id: str) -> None:
        """Rebuild a live in-process gate record for a persisted WAITING_INPUT
        task (R10 boot re-arm, sync, always best-effort). Sources: state.json
        (agent_session + attempt), the task events.jsonl (``gate_opened``
        question) and the session transcript (last user/agent-final messages
        for the resume prompt + the runner agent). Without a session ref the
        record is degraded but still answerable — ``answer()`` then opens a
        FRESH session and resumes with the answer (documented). The timeout
        timer is armed later, in async ``start()`` (fresh FULL
        ``tasks.gate_timeout_s`` — the old asyncio timer is gone)."""
        state_data = self._store.read_state(task_id) or {}
        session_id = state_data.get("agent_session")
        session_id = session_id if isinstance(session_id, str) and session_id.strip() else None
        attempt = state_data.get("attempt")
        try:
            attempt = int(attempt) if attempt is not None else 0
        except (TypeError, ValueError):
            attempt = 0
        raw = self._raw_task_yaml(task_id)
        question = self._persisted_question(task_id)
        agent: str | None = None
        prompt = (
            "## Context\nThe task was parked for agent input before a restart; "
            "the original prompt and transcript are unavailable. Answer the "
            "parked question to continue."
        )
        if session_id is not None:
            try:
                records = read_jsonl(
                    self._manager.sessions_root / session_id / "transcript.jsonl"
                )
            except (OSError, ValueError):
                records = []
            if records:
                prompt = self._resume_prompt_from_transcript(records, question)
                for record in reversed(records):
                    if record.get("kind") == "agent_final":
                        meta = record.get("meta")
                        if isinstance(meta, dict) and isinstance(meta.get("agent"), str):
                            agent = str(meta["agent"])
                        break
        workdir = raw.get("workdir") if isinstance(raw.get("workdir"), str) else None
        description = raw.get("description")
        description = description if isinstance(description, str) else "restored task"
        self._gates[task_id] = {
            "prompt": prompt,
            "analysis": SimpleNamespace(
                mode="gate",
                workdir=workdir,
                need_context=False,
                skill_hints=[],
            ),
            "session": None,
            "session_id": session_id,
            "agent": agent,
            "task_oneway": False,
            "question": question,
            "attempt": attempt,
            "deadline_ts": None,
            "renotified": False,
            "timer": None,
            "restored": True,
        }
        self._raw_note(task_id, {"reason": "gate restored at boot; timer re-arm pending start"})

    async def _arm_restored_gates(self) -> None:
        """Re-arm every restored gate's F15 timer with the FULL fresh
        ``tasks.gate_timeout_s`` window and record the ``restored`` row."""
        for task_id, record in list(self._gates.items()):
            if record.get("timer") is not None:
                continue
            self._arm_gate_timer(task_id, self._gate_timeout_s())
            self._gate_row(task_id, "restored", deadline_ts=record.get("deadline_ts"))

    def _raw_task_yaml(self, task_id: str) -> dict:
        """Best-effort raw task.yaml mapping (schema NOT enforced here — boot
        restore must not fail over a task.yaml that the runner would reject)."""
        try:
            raw = yaml.safe_load(
                (self._store.root / task_id / _TASK_YAML_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _persisted_question(self, task_id: str) -> str:
        """The parked question: the LAST ``gate_opened`` event's question."""
        try:
            events = read_jsonl(self._store.root / task_id / "events.jsonl")
        except (OSError, ValueError):
            return ""
        for event in reversed(events):
            if event.get("kind") == "gate_opened":
                question = (event.get("data") or {}).get("question")
                if isinstance(question, str):
                    return question
        return ""

    def _resume_prompt_from_transcript(self, records: list[dict], question: str) -> str:
        """Documented simplification (R10): the original agent prompt is not
        persisted, so the restored gate resumes with a context summary — the
        parked question plus the transcript's last user message and last
        agent output — instead of the verbatim pre-restart prompt."""
        last_user = ""
        last_agent = ""
        for record in records:
            kind = record.get("kind")
            data = record.get("data")
            if not isinstance(data, dict):
                continue
            if kind == "user":
                last_user = str(data.get("text") or "")
            elif kind == "agent_final" and not (data.get("meta") or {}).get("failed"):
                last_agent = str(data.get("text") or "")
        preamble = (
            "## Context\nThe task was parked for agent input and the process "
            "restarted; the original prompt is approximated from the transcript."
        )
        sections = [preamble]
        if question:
            sections.append(f"### Parked question\n{question}")
        if last_user:
            sections.append(f"### Last user message\n{last_user}")
        if last_agent:
            sections.append(f"### Last agent output\n{last_agent}")
        return "\n\n".join(sections)

    async def _boot_drain(self) -> None:
        pending = list(self._boot_pending)
        self._boot_pending.clear()
        for task_id in pending:
            self._queue.put_nowait(task_id)

    # ------------------------------------------------------------------
    # Watcher (D14): poll inbox, publish appeared once, pick up on capacity
    # ------------------------------------------------------------------
    async def _watch(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self._cfg.tasks.poll_seconds)

    async def _poll_once(self) -> None:
        """One watcher beat (watcher coroutine AND tests call this directly).

        Dedupe choice (F13, documented): ``task.appeared`` is published exactly
        once per id (first sighting); picked-up ids are remembered forever and
        never re-fired; an id left in the inbox for capacity reasons stays
        eligible and is picked up on a later beat WITHOUT re-publishing.
        """
        try:
            inbox_ids = self._store.list_inbox()
        except OSError as exc:
            _LOG.warning("watcher: inbox listing failed: %s", exc)
            return
        for task_id in inbox_ids:
            if task_id in self._picked:
                continue
            if task_id not in self._announced:
                self._announced.add(task_id)
                await self._publish(
                    "task.appeared",
                    {"task_id": task_id, "path": str(self._store.inbox / task_id)},
                )
            if self._capacity_free():
                await self._pick_up(task_id)

    def _capacity_free(self) -> bool:
        cap = max(1, int(self._cfg.tasks.max_concurrent))
        return len(self._workers) + self._queue.qsize() < cap

    async def _pick_up(self, task_id: str) -> None:
        """The pickup move (m2): inbox/<id> -> tasks/<id>, born state.json
        (parent/origin/depth from task.yaml + the parent's state.json), then
        the queue hand-off. TaskDir.birth is idempotent (F17) — a re-pickup
        after a crash never clobbers existing state."""
        try:
            destination = self._store.move_from_inbox(task_id)
        except TaskExistsError:
            self._picked.add(task_id)
            return
        except TaskSchemaError as exc:
            _LOG.warning("watcher: pickup of %s failed: %s", task_id, exc)
            return
        origin, parent = self._read_provenance(destination / _TASK_YAML_FILENAME)
        depth = self._depth_for(parent, task_id)
        TaskDir.birth(
            destination,
            self._cfg,
            origin=origin,
            parent=parent,
            depth=depth,
            bus=self._bus,
        )
        self._picked.add(task_id)
        self._queue.put_nowait(task_id)

    def _read_provenance(self, task_yaml_path: Path) -> tuple[str, str | None]:
        """(origin, parent) read best-effort at pickup; the runner's own
        validation is the authoritative schema check."""
        origin, parent = "user", None
        try:
            raw = yaml.safe_load(task_yaml_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return origin, parent
        if isinstance(raw, dict):
            if raw.get("origin") in ("user", "agent", "scheduler", "api"):
                origin = str(raw["origin"])
            candidate = raw.get("parent")
            if isinstance(candidate, str) and len(candidate) == 8:
                parent = candidate
        return origin, parent

    def _depth_for(self, parent: str | None, task_id: str) -> int:
        """Child depth = parent state.json depth + 1 (D17 chaining); roots 0."""
        if not parent:
            return 0
        parent_state = self._store.read_state(parent)
        if parent_state is None:
            return 0
        try:
            return int(parent_state.get("depth") or 0) + 1
        except (TypeError, ValueError):
            return 0

    def _parent_max_depth(self, task_id: str) -> int | None:
        """The parent's own task.yaml ``max_depth`` override (D17 cap chain)."""
        try:
            raw = yaml.safe_load(
                (self._store.root / task_id / _TASK_YAML_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            return None
        if isinstance(raw, dict):
            value = raw.get("max_depth")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    # ------------------------------------------------------------------
    # Single runner (one consumer loop; §3.5 flow)
    # ------------------------------------------------------------------
    async def _runner(self) -> None:
        # One consumer: a slot is acquired BEFORE the dequeue, so with
        # max_concurrent=1 a second queued task literally waits in the queue.
        while True:
            await self._slots.acquire()
            task_id = await self._queue.get()
            worker = asyncio.create_task(self._run_one(task_id), name=f"taskloop-run-{task_id}")
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)

    async def _run_one(self, task_id: str) -> None:
        task = asyncio.current_task()
        try:
            await self._process(task_id)
        except asyncio.CancelledError:
            _LOG.warning("taskloop: run of %s cancelled (stopping)", task_id)
            raise
        except Exception as exc:  # noqa: BLE001 — resilient loop: one task's failure never crashes
            _LOG.exception("task %s failed with an unexpected error", task_id)
            try:
                await self._fail_task(task_id, f"internal error: {exc}")
            except Exception as fail_exc:  # noqa: BLE001 — reporting failure must not crash
                _LOG.warning("task %s: failure bookkeeping failed: %s", task_id, fail_exc)
        finally:
            if task is not None:
                self._workers.discard(task)
            self._slots.release()

    async def _process(self, task_id: str) -> None:
        """The §3.5 per-task flow: validate -> deps -> analyze -> run."""
        if not (self._store.root / task_id).is_dir():
            _LOG.warning("task %s vanished before execution; skipped", task_id)
            return
        task = self._store.open_task(task_id)
        state = task.state
        if state not in ("appeared", "READY", "ANALYZING"):
            _LOG.warning("task %s dequeued in state %s; skipped (double-enqueue guard)", task_id,
                         state)
            return
        if state == "appeared":
            await task.transition("ANALYZING")
        normalized = await self._validate(task)
        if normalized is None:
            return
        if not await self._deps_gate(task, normalized, fresh=task.state == "ANALYZING"):
            return
        await task.transition("RUNNING")
        resolved = await self._analyze_step(task, normalized)
        if resolved is None:
            return
        analysis, agent = resolved
        session = await self._open_session(task, analysis, agent)
        await self._attempts_loop(
            task, session, analysis, agent, self._assemble_prompt(analysis),
            task_oneway=bool(normalized["oneway"]),
        )

    async def _validate(self, task: TaskDir) -> dict | None:
        """Load + validate task.yaml; invalid -> FAILED(syntax) + events."""
        try:
            return load_task_yaml(
                task.directory / _TASK_YAML_FILENAME,
                max_desc_chars=int(self._cfg.tasks.max_desc_chars),
            )
        except TaskSchemaError as exc:
            reason = f"syntax: {exc}"
            if task.state == "ANALYZING":
                await task.transition("FAILED", reason=reason)
            else:  # requeued READY: the only legal corridor to FAILED is RUNNING
                await task.transition("RUNNING")
                await task.transition("FAILED", reason=reason)
            return None

    async def _deps_gate(self, task: TaskDir, normalized: dict, *, fresh: bool) -> bool:
        """Ready-check with F14 guards: unknown dep / cycle -> FAILED; not
        ready -> ANALYZED -> WAITING_DEPS (parked; re-checked on every parent
        DONE via _recheck_waiting)."""
        task_id = task.id
        try:
            ready = self._store.ready_check(task_id)
        except TaskSchemaError as exc:
            reason = f"dependency/cycle: {exc}"
            if fresh and task.state == "ANALYZING":
                await task.transition("FAILED", reason=reason)
            else:
                await task.transition("RUNNING")
                await task.transition("FAILED", reason=reason)
            return False
        if not ready:
            if task.state == "ANALYZING":
                await task.transition("ANALYZED")
            await task.transition("WAITING_DEPS")
            await task.append_event("dep_wait", {"on": list(normalized["depends_on"])})
            return False
        if task.state == "ANALYZING":
            await task.transition("ANALYZED")
            await task.transition("READY")
        elif task.state != "READY":
            _LOG.warning(
                "task %s: unexpected state %s after deps gate; skipped", task.id, task.state
            )
            return False
        return True

    async def _analyze_step(self, task: TaskDir, normalized: dict) -> tuple[Any, str] | None:
        """D16 analyze + the task.yaml agent-hint OVERRIDE (D15/D16: the file
        advice wins — analysis confirms rather than overrides). ANY exception
        from the injected router is provider-down -> honest FAILED (M-8b)."""
        desc = normalized["description"]
        hint = normalized["agent"]
        try:
            # Direct sync analyze() — blocks the loop (PoC, documented); an
            # async double's coroutine result is awaited.
            result = self._router.analyze(desc, workdir=normalized["workdir"])
            analysis = await result if inspect.isawaitable(result) else result
        except Exception as exc:  # noqa: BLE001 — M-8b: analyze provider failure = task failure
            await self._fail_live(task, f"analyze failed (provider down?): {exc}")
            return None
        analysis_agent = getattr(analysis, "agent", None)
        if hint is not None and analysis_agent != hint:
            analysis = self._override_agent(analysis, hint)
            await task.append_event(
                "note",
                {"note": f"task.yaml agent hint {hint!r} overrides analysis {analysis_agent!r}"},
            )
        return analysis, str(hint or analysis_agent)

    @property
    def _router(self) -> Any:
        if self._injected_router is not None:
            return self._injected_router
        if self._router_obj is None:
            self._router_obj = RouterBrain(self._cfg, bus=self._bus)
        return self._router_obj

    @staticmethod
    def _override_agent(analysis: Any, hint: str) -> Any:
        if hasattr(analysis, "model_copy"):
            return analysis.model_copy(update={"agent": hint})
        analysis.agent = hint
        return analysis

    # ------------------------------------------------------------------
    # Session + prompt (a task's transcript IS a session, ARCH 2.9.4)
    # ------------------------------------------------------------------
    async def _open_session(self, task: TaskDir, analysis: Any, agent: str) -> Session:
        task_title = getattr(analysis, "task_title", "") or ""
        title = f"T<{task.id}>: {(task_title or 'task')[:80]}"
        session = await self._manager.get_or_create(None, title=title)
        await self._safe_move(session, "routing")
        AtomicCounter(task.directory / _STATE_FILENAME).set(
            {"agent_session": session.id, "updated": _utc_now()}
        )
        return session

    def _assemble_prompt(self, analysis: Any) -> str:
        """Gateway _assemble_prompt mirror (simplified): memory digests when
        ``need_context``, skill bodies for ``skill_hints`` — both best-effort
        try/except — and ``prompt_seed`` ALWAYS last."""
        sections: list[str] = []
        if getattr(analysis, "need_context", False):
            try:
                engine = MemoryEngine(self._cfg, self._bus)
                digests = [engine.digest(name) for name in ("MEMORY", "USER", "lessons")]
                memory = "\n".join(digest for digest in digests if digest)
                if memory:
                    sections.append(f"### Memory digest\n{memory}")
            except Exception as exc:  # noqa: BLE001 — enrichment must never block a run
                _LOG.warning("task prompt: memory digests unavailable: %s", exc)
        hints = getattr(analysis, "skill_hints", None) or []
        if hints:
            try:
                engine = SkillsEngine(self._cfg)
                bodies = []
                for name in hints[:3]:
                    body = engine.get_skill_body(name)
                    if body:
                        bodies.append(f"#### skill: {name}\n{body}")
                if bodies:
                    sections.append("### Skills\n" + "\n\n".join(bodies))
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("task prompt: skill bodies unavailable: %s", exc)
        sections.append(f"## Input\n{getattr(analysis, 'prompt_seed', '')}")
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Run attempts (M-8) + stream relay + gate park
    # ------------------------------------------------------------------
    async def _attempts_loop(
        self,
        task: TaskDir,
        session: Session,
        analysis: Any,
        agent: str,
        prompt: str,
        task_oneway: bool = False,
    ) -> None:
        gate_allowed = getattr(analysis, "mode", "oneway") == "gate" and not task_oneway
        retries = max(0, int(self._cfg.tasks.retries))
        attempt = task.bump_attempt()
        while True:
            self._ledger_row(task.id, agent, attempt, "RUNNING")
            spec = RunSpec(
                prompt=prompt,
                profile=None,  # PoC: profile stays None (hermes adapter default)
                workdir=getattr(analysis, "workdir", None),
                resume_ref=session.agents_refs.get(agent),
                timeout_s=self._timeout_for(agent),
                oneway=not gate_allowed,
            )
            await self._to_agent_running(session)
            started = time.monotonic()
            outcome, payload = await self._stream_once(task, session, agent, spec, gate_allowed)
            if outcome == "gate":
                self._ledger_row(task.id, agent, attempt, "WAITING_INPUT")
                await task.append_event("gate_opened", {"question": payload})
                await self._publish(
                    "gate.opened", {"task_id": task.id, "question": payload}
                )
                await task.transition("WAITING_INPUT")
                self._gates[task.id] = {
                    "prompt": prompt,
                    "analysis": analysis,
                    "session": session,
                    "session_id": session.id,
                    "agent": agent,
                    "task_oneway": task_oneway,
                    "question": payload,
                    "attempt": int(attempt),
                    "deadline_ts": None,
                    "renotified": False,
                    "timer": None,
                    "restored": False,
                }
                # F15: exactly one 24 h timer per parked gate.
                timeout_s = self._gate_timeout_s()
                self._arm_gate_timer(task.id, timeout_s)
                self._gate_row(task.id, "opened", deadline_ts=self._gates[task.id]["deadline_ts"])
                return
            if outcome == "done":
                self._ledger_row(task.id, agent, attempt, "DONE")
                await self._finalize(task, session, agent, payload, started)
                return
            # failed: M-8 retries (agent + non-agent errors alike)
            self._ledger_row(task.id, agent, attempt, "FAILED")
            if attempt <= retries:
                attempt = task.bump_attempt()
                continue
            await self._fail_live(task, f"agent error after {attempt} attempt(s): {payload}")
            return

    def _ledger_row(self, task_id: str, agent: str, attempt: int, state: str) -> None:
        """One runs-ledger row per runner adapter run (row 23 chain): the
        gateway /materials consumer reads these as {ts, session_id|task_id,
        state|outcome} — never block a run on ledger failure."""
        try:
            JsonlWriter(
                Path(self._cfg.storage.data_dir) / "state" / _RUNS_LEDGER_FILENAME
            ).append(
                {
                    "ts": _utc_now(),
                    "task_id": task_id,
                    "agent": agent,
                    "attempt": int(attempt),
                    "state": state,
                }
            )
        except (OSError, TypeError, ValueError) as exc:
            _LOG.warning("runs ledger append for task %s failed: %s", task_id, exc)

    def _timeout_for(self, agent: str) -> int | None:
        conf = getattr(self._cfg.agents, agent, None)
        return getattr(conf, "timeout_s", None)

    async def _stream_once(
        self,
        task: TaskDir,
        session: Session,
        agent: str,
        spec: RunSpec,
        gate_allowed: bool,
    ) -> tuple[str, str]:
        """Consume ONE adapter stream (run failure retry = another call).
        Returns ``("done", final_text)``, ``("failed", error)`` or
        ``("gate", question)``. Terminal moves agent_running -> relaying ->
        idle mirror the gateway _run_agent pattern; a gate parks WITHOUT
        moving (the session stays agent_running for boot replay)."""
        adapter = self._resolve_adapter(agent)
        seq = 0
        try:
            async for event in adapter.run(spec):
                if event.kind == "started":
                    continue
                if event.kind in ("chunk", "tool"):
                    if event.kind == "tool":
                        text = f"[tool] {event.tool_brief or event.tool_name or '?'}"
                    else:
                        text = event.text or ""
                    seq += 1
                    await self._publish(
                        "reply.chunk",
                        {"session_id": session.id, "seq": seq, "text": text, "source": "agent"},
                    )
                    await session.append_event(
                        "agent_chunk", {"seq": seq, "text": text, "source": "agent"}
                    )
                    await task.append_event("note", {"chunk": seq, "text": text})
                elif event.kind == "final":
                    final_text = event.final_text or ""
                    await session.append_event(
                        "agent_final", {"text": final_text, "meta": {"agent": agent}}
                    )
                    if event.session_ref:
                        await session.set_agent_ref(agent, event.session_ref)
                    await self._publish(
                        "agent.done", {"session_id": session.id, "final_text": final_text}
                    )
                    await self._relay_idle(session)
                    return "done", final_text
                elif event.kind == "failed":
                    await session.append_event(
                        "agent_final",
                        {
                            "text": f"[run failed] {event.error or 'unknown error'}",
                            "meta": {"agent": agent, "failed": True},
                        },
                    )
                    await self._relay_idle(session)
                    return "failed", event.error or "unknown error"
                elif event.kind == "system":
                    question = self._gate_question(event)
                    if question is not None:
                        if gate_allowed:
                            return "gate", question  # stream suspended; session parked live
                        await task.append_event(
                            "note",
                            {"note": "gate question ignored (oneway or analysis mode != gate)"},
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — honest terminal, never crash the loop
            _LOG.exception("task %s: adapter stream error", task.id)
            await self._relay_idle(session)
            return "failed", f"internal error: {exc}"
        await self._relay_idle(session)
        return "failed", "adapter stream ended without a final event"

    @staticmethod
    def _gate_question(event: Any) -> str | None:
        """A fake-adapter system event carries the PoC gate question as raw
        JSON ``{"gate_question": "..."}`` (real GATE blocks are R10)."""
        raw = getattr(event, "raw", None)
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, dict):
            question = data.get("gate_question")
            if isinstance(question, str) and question.strip():
                return question
        return None

    async def _to_agent_running(self, session: Session) -> None:
        if session.state in ("created", "failed"):
            await self._safe_move(session, "routing")
        if session.state == "idle":
            await self._safe_move(session, "routing")
        if session.state == "routing":
            await self._safe_move(session, "agent_running")

    async def _relay_idle(self, session: Session) -> None:
        if session.state == "agent_running":
            await self._safe_move(session, "relaying")
        if session.state == "relaying":
            await self._safe_move(session, "idle")

    async def _safe_move(self, session: Session, new_state: str) -> None:
        try:
            await self._manager.move(session, new_state)
        except (SessionFSMError, ValueError) as exc:
            _LOG.warning("session %s move to %s skipped: %s", session.id, new_state, exc)

    async def _publish(self, topic: str, payload: dict) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(topic, payload)
        except Exception as exc:  # noqa: BLE001 — bus failures must not break the loop
            _LOG.warning("bus publish failed for %s: %s", topic, exc)

    async def _subscriber_loop(self) -> None:
        """Optional bus sink consumer: task.state DONE events trigger the
        WAITING_DEPS re-check (the direct recheck after own DONE is the
        primary, duck-typed-bus-compatible mechanism) and gate.answered
        events (the Discord path) drive the SAME answer/resume as the HTTP
        route (R10). One bus line per loop iteration."""
        queue = self._sink_queue
        if queue is None and self._sink_name is not None:
            # Duck-typed bus fallback (start() already holds the sink queue
            # for the real Bus; a second subscribe on the same name raises).
            try:
                queue = self._bus.subscribe(self._sink_name)
            except ValueError:
                return
        if queue is None:
            return
        while True:
            topic, payload = await queue.get()
            if topic == "task.state" and payload.get("new") == "DONE":
                await self._recheck_waiting()
            elif topic == "gate.answered":
                await self._handle_gate_answered(payload if isinstance(payload, dict) else {})

    async def _handle_gate_answered(self, payload: dict) -> None:
        """One gate.answered bus line -> self.answer() when the payload's
        task_id matches a LIVE gate. The Discord parity grammar is
        ``answer T<id> <text>`` and the bot publishes task_id as ``T<id>``,
        so the leading ``T`` is stripped to match the raw 8-char gate id.
        Failures are swallowed to one warning line (resilient loop)."""
        task_id = payload.get("task_id")
        answer_text = payload.get("answer")
        if not isinstance(task_id, str) or not task_id:
            return
        if not isinstance(answer_text, str) or not answer_text.strip():
            return
        candidates = [task_id]
        if task_id.startswith("T") and len(task_id) > 1:
            candidates.append(task_id[1:])
        for candidate in candidates:
            if candidate not in self._gates:
                continue
            try:
                await self.answer(candidate, answer_text)
            except Exception as exc:  # noqa: BLE001 — one line per event
                _LOG.warning("gate.answered resume for task %s failed: %s", candidate, exc)
            return

    # ------------------------------------------------------------------
    # DONE + SUBTASK_DROP (D17)
    # ------------------------------------------------------------------
    async def _finalize(
        self, task: TaskDir, session: Session, agent: str, final_text: str, started: float
    ) -> None:
        for block in parse_blocks(final_text):
            if block.tag == "SUBTASK_DROP":
                await self._handle_drop(task, block)
        stripped = strip_blocks(final_text)
        task.write_result(stripped, agent=agent, duration_s=round(time.monotonic() - started, 3))
        await task.transition("DONE")
        # Children re-check: the bus subscriber handles it when available; the
        # direct re-check ALSO runs for duck-typed buses (documented choice).
        await self._recheck_waiting()
        self._drop_gate(task.id)

    async def _handle_drop(self, task: TaskDir, block: Any) -> None:
        """One parsed SUBTASK_DROP block: refusals are honest events; accepted
        drops write an INBOX task (the child waits for the WATCHER — never run
        inline, S1.5). Depth cap per D17: cap = parent_max_depth override or
        the global default (floor 1); cap<=0 or child depth beyond cap -> refused."""
        if not block.ok or not isinstance(block.fields, dict):
            await task.append_event(
                "subtask_refused", {"reason": block.reason or "unparseable SUBTASK_DROP block"}
            )
            return
        fields = dict(block.fields)
        oneway = False
        if "oneway" in fields:
            value = fields.pop("oneway")
            if not isinstance(value, bool):
                await task.append_event(
                    "subtask_refused", {"reason": "field 'oneway' must be a boolean"}
                )
                return
            oneway = value
        workdir = None
        if "workdir" in fields:
            value = fields.pop("workdir")
            if (
                not isinstance(value, str)
                or not value.startswith(_WORKDIR_PREFIX)
                or not Path(value).is_dir()
            ):
                await task.append_event(
                    "subtask_refused",
                    {"reason": f"field 'workdir' must be an existing {_WORKDIR_PREFIX} dir"},
                )
                return
            workdir = value
        # WP4 demand seam: `schedule` is popped BEFORE validate_subtask_drop
        # (same pattern as oneway/workdir — _SUBTASK_DROP_KEYS stays unchanged):
        # a non-empty mapping DEMANDS a scheduled follow-up INSTEAD of an
        # immediate subtask scheduling; the engine validates the shape later.
        schedule_demand: dict | None = None
        if "schedule" in fields:
            value = fields.pop("schedule")
            if not isinstance(value, dict) or not value:
                await task.append_event(
                    "subtask_refused", {"reason": "field 'schedule' must be a non-empty mapping"}
                )
                return
            schedule_demand = value
        try:
            normalized = validate_subtask_drop(
                fields, max_desc_chars=int(self._cfg.tasks.max_desc_chars)
            )
        except TaskSchemaError as exc:
            await task.append_event("subtask_refused", {"reason": str(exc)})
            return
        parent_state = self._store.read_state(task.id) or {}
        try:
            parent_depth = int(parent_state.get("depth") or 0)
        except (TypeError, ValueError):
            parent_depth = 0
        cap = subtask_depth_cap(
            self._parent_max_depth(task.id),
            default_max_depth=int(self._cfg.tasks.max_depth),
        )
        child_depth = parent_depth + 1
        if cap <= 0 or child_depth > cap:
            await task.append_event(
                "subtask_refused",
                {"reason": f"depth cap exceeded: depth {child_depth} > max_depth {cap}"},
            )
            return
        try:
            child_id = self._store.create_inbox_task(
                normalized["description"],
                origin="agent",
                parent=task.id,
                depends_on=normalized["depends_on"] or None,
                agent=normalized.get("agent"),
                workdir=workdir,
                oneway=oneway,
            )
        except (TaskSchemaError, TaskExistsError) as exc:
            await task.append_event("subtask_refused", {"reason": str(exc)})
            return
        if schedule_demand is not None:
            if self._scheduler_sink is None:
                await task.append_event(
                    "subtask_schedule_note",
                    {
                        "reason": "schedule demand recorded but no scheduler sink wired",
                        "schedule": schedule_demand,
                    },
                )
            else:
                try:
                    await self._scheduler_sink(
                        {
                            "task_id": child_id,
                            "schedule": schedule_demand,
                            "description": normalized["description"],
                            "origin_task": task.id,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — a broken sink must not break the drop
                    await task.append_event(
                        "subtask_schedule_note", {"reason": f"scheduler sink failed: {exc}"}
                    )
                else:
                    await task.append_event(
                        "subtask_schedule_accepted",
                        {"task_id": child_id, "schedule": schedule_demand},
                    )
        await task.append_event("subtask_drop", {"child": child_id, "parent": task.id})
        _LOG.info("task %s: SUBTASK_DROP -> inbox child %s", task.id, child_id)

    async def _recheck_waiting(self) -> None:
        """Re-check every WAITING_DEPS task; ready ones -> READY + re-queue."""
        try:
            states = self._store.list_tasks()
        except OSError as exc:
            _LOG.warning("waiting re-check failed: %s", exc)
            return
        for task_id, state in sorted(states.items()):
            if state != "WAITING_DEPS":
                continue
            try:
                if not self._store.ready_check(task_id):
                    continue
                task = self._store.open_task(task_id)
                if task.state != "WAITING_DEPS":
                    continue
                await task.transition("READY")
            except TaskSchemaError as exc:
                _LOG.warning("waiting re-check for %s failed: %s", task_id, exc)
                continue
            self._queue.put_nowait(task_id)

    # ------------------------------------------------------------------
    # Failure + adapter seam
    # ------------------------------------------------------------------
    async def _fail_live(self, task: TaskDir, reason: str) -> None:
        state = task.state
        if state in ("DONE", "FAILED"):
            await task.append_event("note", {"note": reason})
            return
        if "FAILED" in TRANSITIONS.get(state, set()):
            await task.transition("FAILED", reason=reason)
            return
        # No legal corridor to FAILED (e.g. READY): park honestly + note.
        await task.append_event("note", {"note": f"failure while state={state}: {reason}"})

    async def _fail_task(self, task_id: str, reason: str) -> None:
        """Bookkeeping for a task whose TaskDir handle is gone (best effort)."""
        if not (self._store.root / task_id / _STATE_FILENAME).is_file():
            return
        try:
            task = self._store.open_task(task_id)
            await self._fail_live(task, reason)
        except TaskSchemaError as exc:
            _LOG.warning("task %s: failure bookkeeping failed: %s", task_id, exc)

    def _resolve_adapter(self, agent: str) -> Any:
        """The adapter seam (cached per agent — the row-23 spy point)."""
        cached = self._adapter_cache.get(agent)
        if cached is not None:
            return cached
        if self._adapter_factory is not None:
            adapter = self._adapter_factory(agent)
        elif self._adapters is None:
            adapter = registry_get(agent, self._cfg)
        elif callable(self._adapters):
            adapter = self._adapters(agent)
        else:
            entry = self._adapters.get(agent)
            if entry is None:
                raise ValueError(f"no adapter wired for agent {agent!r}")
            adapter = entry(agent) if callable(entry) and not hasattr(entry, "run") else entry
        if adapter is None:
            raise ValueError(f"no adapter wired for agent {agent!r}")
        self._adapter_cache[agent] = adapter
        return adapter


