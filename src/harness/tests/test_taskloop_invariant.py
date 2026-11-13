"""Row-23 invariant spy battery (TEST_PLAN row 23, harness-as-sole-invoker
S1.5): only the task-loop runner (and the chat path for chat sessions) ever
calls adapter.run; agents' output can only create inbox files via
SUBTASK_DROP (no invocation side effects); the full chain user->T1->(drop)->
T2->(drop)->T3 stays attributed to the runner and lands in the runs ledger;
depth-cap drops are refused; boot replay never invokes agents.

Fixture patterns mirror tests/test_taskloop.py (fake loop + router seams).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from harness.adapters.base import RunSpec, StreamEvent
from harness.bus import Bus
from harness.gateway.app import create_app
from harness.persist import read_jsonl
from harness.router import Analysis, RouterBrain
from harness.session_manager import SessionManager
from harness.taskloop import TaskLoop
from harness.tasks import TaskDir, TaskStore
from tests.mocks.router_model import make_fake_model

_T1 = "ABA23456"

_API_KEY = "test-key-123"  # conftest tmp_volume feeds HARNESS_API_KEY with this
_HEADERS = {"X-API-Key": _API_KEY}


def _analysis(seed: str, *, agent: str = "hermes", mode: str = "oneway") -> Analysis:
    return Analysis(
        agent=agent,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        workdir="/app/projects",
        prompt_seed=seed,
        task_title=seed[:40],
    )


class _Router:
    def __init__(self, result: Analysis | Exception | None = None) -> None:
        self.result = result if result is not None else _analysis("seed")

    def analyze(self, task_desc: str, *, workdir: str | None = None) -> Analysis:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result.model_copy(update={"prompt_seed": task_desc})


class _SpyAdapter:
    """Row-23 recording wrapper adapter: every ``run`` increments the counter
    and records its assigned caller tag; the stream itself is scripted via
    ``planner(spec) -> [(delay_s, StreamEvent)]``."""

    def __init__(self, caller: str, calls: list[dict], planner: Any) -> None:
        self._caller = caller
        self._calls = calls
        self._planner = planner

    @property
    def count(self) -> int:
        return len(self._calls)

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self._calls.append(
            {
                "caller": self._caller,
                "prompt": spec.prompt,
                "workdir": spec.workdir,
                "oneway": spec.oneway,
                "resume_ref": spec.resume_ref,
            }
        )

        async def gen() -> AsyncIterator[StreamEvent]:
            for delay, event in self._planner(spec):
                if delay:
                    await asyncio.sleep(delay)
                yield event

        return gen()


def _final(text: str, **kwargs: Any) -> StreamEvent:
    return StreamEvent(kind="final", final_text=text, **kwargs)


def _drop(text: str) -> str:
    return f"```SUBTASK_DROP\ndescription: {text}\n```"


def _tweak(cfg: Any, **tasks_updates: Any) -> Any:
    return cfg.model_copy(update={"tasks": cfg.tasks.model_copy(update=tasks_updates)})


def _events_of(store: Any, task_id: str) -> list[dict]:
    return read_jsonl(store.root / task_id / "events.jsonl")


def _drop_child(store: Any, task_id: str) -> str:
    children = [
        event["data"]["child"]
        for event in _events_of(store, task_id)
        if event["kind"] == "subtask_drop"
    ]
    assert len(children) == 1, f"expected exactly one SUBTASK_DROP child of {task_id}"
    return str(children[0])


def _states_of(store: Any, task_id: str) -> list[str]:
    changes = [
        event["data"].get("new")
        for event in _events_of(store, task_id)
        if event["kind"] == "state_change"
    ]
    return [str(state) for state in changes if state is not None]


def _task_dirs(store: Any) -> set[str]:
    if not store.root.is_dir():
        return set()
    return {entry.name for entry in store.root.iterdir() if entry.is_dir()}


def _session_names(cfg: Any) -> set[str]:
    sessions_root = Path(cfg.storage.data_dir) / "sessions"
    if not sessions_root.is_dir():
        return set()
    return {entry.name for entry in sessions_root.iterdir() if entry.is_dir()}


def _session_meta(cfg: Any, session_id: str) -> dict:
    path = Path(cfg.storage.data_dir) / "sessions" / session_id / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _set_state(cfg: Any, task_id: str, state: str, **extra: Any) -> None:
    path = Path(cfg.storage.data_dir) / "tasks" / task_id / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update({"state": state, **extra})
    path.write_text(json.dumps(data), encoding="utf-8")


async def _wait_state(store: Any, task_id: str, want: str, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"task {task_id} never reached {want}; got {store.read_state(task_id)}")


def _runs_rows(cfg: Any) -> list[dict]:
    return read_jsonl(Path(cfg.storage.data_dir) / "state" / "runs.jsonl")


def _planner_for(markers: dict[str, str]) -> Any:
    """planner(spec): first matching marker -> [(0.0, _final(text))]."""

    def planner(spec: RunSpec) -> list[tuple[float, StreamEvent]]:
        for marker, text in markers.items():
            if marker in spec.prompt:
                return [(0.0, _final(text))]
        return [(0.0, _final("no-marker final"))]

    return planner


# ---------------------------------------------------------------------------
# 1. Adapter-seam spy (a+b): the run spec flows through the loop's resolution
#    path; nothing else calls adapter.run during the T1 run (count == 1).
# ---------------------------------------------------------------------------
async def test_adapter_seam_spy_attribution(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=3.0)  # the dropped child stays in the inbox
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("root drop chore", origin="user", task_id=_T1)
    calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter(
            "taskloop-runner", calls, _planner_for({"root drop chore": _drop("queued child chore")})
        ),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        # (a) exactly ONE adapter.run, carrying the caller tag == the runner and
        # the RunSpec from the loop's own resolution path (prompt built from the
        # task description via the analysis seed).
        assert len(calls) == 1
        call = calls[0]
        assert call["caller"] == "taskloop-runner"
        assert "root drop chore" in call["prompt"]
        assert call["workdir"] == "/app/projects" and call["oneway"] is True
        # (b) at end of T1 nothing else called adapter.run: the drop artifact is
        # ONLY the inbox child (watcher poll far away), the counter stays at 1.
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        children = store.list_inbox()
        assert len(children) == 1
        child = children[0]
        payload = yaml.safe_load(
            (store.inbox / child / "task.yaml").read_text(encoding="utf-8")
        )
        assert payload["parent"] == _T1 and payload["origin"] == "agent"
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 2. Chat-path exemption: the gateway chat adapter's run is attributed to the
#    gateway only (different adapter OBJECT); the taskloop spy stays at 0.
# ---------------------------------------------------------------------------
async def test_chat_path_exempt_from_taskloop_runner(cfg) -> None:
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    brain = RouterBrain(
        cfg, model=make_fake_model([{"agent": "opencode", "task_title": "chat demo"}])
    )
    chat_calls: list[dict] = []
    gateway_spy = _SpyAdapter(
        "gateway-chat",
        chat_calls,
        lambda spec: [
            (0.02, StreamEvent(kind="chunk", text="hello")),
            (0.02, _final("chat roundtrip done")),
        ],
    )
    app = create_app(
        cfg, bus, manager, router_brain=brain, adapters={"opencode": gateway_spy,
                                                         "hermes": gateway_spy}
    )
    store = TaskStore(cfg, bus=bus)
    loop_calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        manager,
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter(
            "taskloop-runner", loop_calls, _planner_for({})
        ),
        router=_Router(),
    )
    loop.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/chat", json={"text": "tell me a haiku"}, headers=_HEADERS
            )
            assert response.status_code == 200
            session_id = response.json()["session_id"]
            deadline = time.monotonic() + 8.0
            state = ""
            while time.monotonic() < deadline:
                probe = await client.get(f"/api/session/{session_id}", headers=_HEADERS)
                state = probe.json().get("state")
                if state == "idle":
                    break
                await asyncio.sleep(0.05)
            assert state == "idle", f"chat session never went idle ({state})"
        # the CHAT adapter ran exactly once for the chat session...
        assert gateway_spy.count == 1
        assert chat_calls[0]["caller"] == "gateway-chat"
        assert "tell me a haiku" in chat_calls[0]["prompt"]
        # ...while the taskloop spy counter stays 0 for chat (different object:
        # per-adapter attribution, no loop route, no task spawned).
        assert loop_calls == []
        assert store.list_inbox() == []
        assert _task_dirs(store) <= {"inbox"}
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 3. SUBTASK_DROP side-effect audit: the T1 run's only new artifact is the
#    inbox child; the adapter was called ONCE (child NOT run inline).
# ---------------------------------------------------------------------------
async def test_subtask_drop_side_effect_audit(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=3.0)  # the child stays parked in the inbox
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("audit drop chore", origin="user", task_id=_T1)
    tasks_before = _task_dirs(store)
    sessions_before = _session_names(cfg)
    calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter(
            "taskloop-runner", calls, _planner_for({"audit drop chore": _drop("audited child chore")})
        ),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
    finally:
        await loop.stop()
    assert len(calls) == 1 and calls[0]["caller"] == "taskloop-runner"
    assert "audit drop chore" in calls[0]["prompt"]
    # the ONLY new task artifact: T1's own picked-up dir (+ the inbox child).
    assert _task_dirs(store) - tasks_before == {_T1}
    children = store.list_inbox()
    assert len(children) == 1
    child = children[0]
    payload = yaml.safe_load((store.inbox / child / "task.yaml").read_text(encoding="utf-8"))
    assert payload["parent"] == _T1 and payload["origin"] == "agent"
    # no session spawned for the child: exactly ONE session (T1's own).
    new_sessions = _session_names(cfg) - sessions_before
    assert len(new_sessions) == 1
    assert _session_meta(cfg, next(iter(new_sessions)))["title"].startswith(f"T<{_T1}>:")
    assert any(
        event["kind"] == "subtask_drop" and event["data"]["child"] == child
        for event in _events_of(store, _T1)
    )


# ---------------------------------------------------------------------------
# 4. Full chain E2E with spy + ledger: T1 -> (drop) -> T2 -> (drop) -> T3, all
#    DONE; the runner-invoker counter == 3 and runs.jsonl shows the chain.
# ---------------------------------------------------------------------------
async def test_full_chain_spy_and_runs_ledger(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=0.05)  # max_concurrent default 1
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("chain root marker", origin="user", task_id=_T1)
    markers = {
        "chain root marker": _drop("chain mid marker"),
        "chain mid marker": _drop("chain leaf marker"),
        "chain leaf marker": "leaf all done",
    }
    calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter("taskloop-runner", calls, _planner_for(markers)),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        first_child = _drop_child(store, _T1)
        await _wait_state(store, first_child, "DONE")
        second_child = _drop_child(store, first_child)
        await _wait_state(store, second_child, "DONE")
        assert store.list_inbox() == []
    finally:
        await loop.stop()
    chain = [_T1, first_child, second_child]
    # the runner-invoker counter == 3: one adapter.run per task, zero extra.
    assert len(calls) == 3
    assert [call["caller"] for call in calls] == ["taskloop-runner"] * 3
    for marker in ("chain root marker", "chain mid marker", "chain leaf marker"):
        assert any(marker in call["prompt"] for call in calls)
    # per task: a run/agent-started line and the final DONE state.
    for task_id in chain:
        states = _states_of(store, task_id)
        assert "RUNNING" in states and states[-1] == "DONE"
        session_id = store.read_state(task_id)["agent_session"]
        transcript = read_jsonl(
            Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl"
        )
        assert any(record.get("state") == "agent_running" for record in transcript)
        assert store.read_state(task_id)["depth"] == chain.index(task_id)
    # the RUNS LEDGER shows the task chain: one RUNNING + one DONE row per run.
    rows = _runs_rows(cfg)
    assert [row["task_id"] for row in rows] == [
        task_id for task_id in chain for _ in range(2)
    ]
    assert [row["state"] for row in rows] == ["RUNNING", "DONE"] * 3
    assert {row["agent"] for row in rows} == {"hermes"}
    assert all(row["attempt"] == 1 for row in rows)
    assert all(row["ts"] for row in rows)
    assert all(set(row) == {"ts", "task_id", "agent", "attempt", "state"} for row in rows)


# ---------------------------------------------------------------------------
# 5. Depth-cap refusal inside the spy battery: a task AT cfg.tasks.max_depth
#    emits a drop -> subtask_refused, no child, adapter called exactly once.
# ---------------------------------------------------------------------------
async def test_depth_cap_refusal_in_spy_battery(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=0.05, max_depth=2)
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("capped root chore", origin="user", task_id=_T1)
    TaskDir.birth(store.move_from_inbox(_T1), cfg, depth=int(cfg.tasks.max_depth))
    assert store.read_state(_T1)["depth"] == int(cfg.tasks.max_depth)
    calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter(
            "taskloop-runner", calls, _planner_for({"capped root chore": _drop("overflow child chore")})
        ),
        router=_Router(),
    )
    loop._boot_pending = [_T1]
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
    finally:
        await loop.stop()
    refused = [
        event for event in _events_of(store, _T1) if event["kind"] == "subtask_refused"
    ]
    assert len(refused) == 1
    assert refused[0]["data"]["reason"] == "depth cap exceeded: depth 3 > max_depth 2"
    assert not any(event["kind"] == "subtask_drop" for event in _events_of(store, _T1))
    assert store.list_inbox() == []
    assert _task_dirs(store) == {"inbox", _T1}  # no child dir created
    # the at-cap task itself still ran exactly once; no child run.
    assert len(calls) == 1 and calls[0]["caller"] == "taskloop-runner"
    assert "capped root chore" in calls[0]["prompt"]


# ---------------------------------------------------------------------------
# 6. Boot replay never runs agents: a RUNNING task + fake adapters -> spy at 0.
# ---------------------------------------------------------------------------
async def test_replay_never_invokes_adapters(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("replay no-run chore", origin="user", task_id=_T1)
    TaskDir.birth(store.move_from_inbox(_T1), cfg)
    _set_state(cfg, _T1, "RUNNING")
    calls: list[dict] = []
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: _SpyAdapter(
            "taskloop-runner", calls, _planner_for({})
        ),
        router=_Router(),
    )
    counts = loop.replay()  # RUNNING without a live session: requeued once
    assert counts["requeued"] == 1 and counts["failed_restarted"] == 0
    assert store.read_state(_T1)["state"] == "READY"
    counts_2 = loop.replay()  # idempotent: a second replay requeues nothing
    assert counts_2["requeued"] == 0
    # the boot replay (and nothing else) ran: the spy counter stays 0.
    assert calls == []
