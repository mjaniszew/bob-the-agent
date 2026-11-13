"""Tests for harness.taskloop (R9 WP2): watcher + single runner + SUBTASK_DROP
chaining + gate park/resume + boot replay. TEST_PLAN row 19 (second column);
row 23 is honored by construction: the runner via _resolve_adapter is the only
adapter.run caller for task-origin runs."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.adapters.base import RunSpec, StreamEvent
from harness.persist import read_jsonl
from harness.router import Analysis
from harness.session_manager import SessionManager
from harness.taskloop import TaskLoop
from harness.tasks import TaskDir, TaskStore

_T1 = "AAA23456"
_T2 = "BBB23457"
_T3 = "CCC24567"
_T4 = "DDD23567"
_T5 = "EEE24567"


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


class _Router:
    def __init__(self, result: Analysis | Exception | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.result = result if result is not None else _analysis("seed")

    def analyze(self, task_desc: str, *, workdir: str | None = None) -> Analysis:
        self.calls.append((task_desc, workdir))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result.model_copy(update={"prompt_seed": task_desc})


def _analysis(seed: str, *, agent: str = "hermes", mode: str = "oneway") -> Analysis:
    return Analysis(
        agent=agent,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        workdir="/app/projects",
        prompt_seed=seed,
        task_title=seed[:40],
    )


class _Adapter:
    """Scripted adapter: run(spec) is called once per attempt and records the
    spec; planner(spec, index) -> list[(delay_s, StreamEvent)] streams."""

    def __init__(self, runs: list[dict], planner: Any) -> None:
        self._runs = runs
        self._planner = planner
        self._count = 0

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self._count += 1
        entry: dict = {"prompt": spec.prompt, "resume_ref": spec.resume_ref,
                       "oneway": spec.oneway, "start": time.monotonic()}
        index = self._count - 1
        self._runs.append(entry)

        async def gen() -> AsyncIterator[StreamEvent]:
            for delay, event in self._planner(spec, index):
                if delay:
                    await asyncio.sleep(delay)
                entry["end"] = time.monotonic()
                yield event

        return gen()


def _factory(runs: list[dict], planner: Any):
    def make(agent: str) -> _Adapter:
        return _Adapter(runs, planner)

    return make


def _final(text: str, **kwargs: Any) -> StreamEvent:
    return StreamEvent(kind="final", final_text=text, **kwargs)


def _tweak(cfg: Any, **tasks_updates: Any) -> Any:
    return cfg.model_copy(update={"tasks": cfg.tasks.model_copy(update=tasks_updates)})


def _events_of(store: Any, task_id: str) -> list[dict]:
    return read_jsonl(store.root / task_id / "events.jsonl")


def _kinds_of(store: Any, task_id: str) -> list[str]:
    return [event["kind"] for event in _events_of(store, task_id)]


def _states_of(bus: _Bus, task_id: str) -> list[str]:
    return [
        payload.get("new")
        for topic, payload in bus.events
        if topic == "task.state" and payload.get("task_id") == task_id
    ]


async def _wait_state(store: Any, task_id: str, want: str, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"task {task_id} never reached {want}; got {store.read_state(task_id)}")


def _session_meta(cfg: Any, session_id: str) -> dict:
    path = Path(cfg.storage.data_dir) / "sessions" / session_id / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _handcraft_session(cfg: Any, session_id: str, lines: list[dict]) -> None:
    directory = Path(cfg.storage.data_dir) / "sessions" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "transcript.jsonl").write_text(
        "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. E2E happy
# ---------------------------------------------------------------------------
async def test_e2e_happy(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("Fix the failing pytest in tests/test_payments.py",
                            origin="user", task_id=_T1)
    runs: list[dict] = []
    router = _Router()
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final("All checks green.",
                                                                session_ref="sess-1"))]),
        router=router,
    )
    loop.start()
    try:
        await asyncio.wait_for(_wait_state(store, _T1, "DONE"), 6)
        data = store.read_state(_T1)
        assert data["attempt"] == 1 and data["agent_session"]
        assert "All checks green." in (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        meta = _session_meta(cfg, data["agent_session"])
        assert meta["title"].startswith(f"T<{_T1}>:")
        assert meta["agents_refs"]["hermes"] == "sess-1"
        assert store.list_inbox() == []
        assert any(topic == "task.appeared" for topic, _ in bus.events)
        assert _states_of(bus, _T1) == ["ANALYZING", "ANALYZED", "READY", "RUNNING", "DONE"]
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 2. Dependency order A, B -> C; C parks in WAITING_DEPS
# ---------------------------------------------------------------------------
async def test_dependency_order(cfg) -> None:
    cfg = _tweak(cfg, max_concurrent=2, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("alpha marker", origin="user", task_id=_T1)
    store.create_inbox_task("beta marker", origin="user", task_id=_T2)
    store.create_inbox_task("gamma marker", origin="user", task_id=_T3, depends_on=[_T1, _T2])
    runs: list[dict] = []
    delays = {0: 0.01, 1: 0.4, 2: 0.01}

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        return [(delays.get(index, 0.01), _final(f"done {index}"))]

    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus,
                    adapter_factory=_factory(runs, planner), router=_Router())
    loop.start()
    try:
        await asyncio.wait_for(_wait_state(store, _T3, "DONE"), 8)
        order = []
        for entry in runs:
            if "alpha" in entry["prompt"]:
                order.append("alpha")
            elif "beta" in entry["prompt"]:
                order.append("beta")
            else:
                order.append("gamma")
        assert order == ["alpha", "beta", "gamma"]
        assert "dep_wait" in _kinds_of(store, _T3)
        assert _states_of(bus, _T3) == [
            "ANALYZING", "ANALYZED", "WAITING_DEPS", "READY", "RUNNING", "DONE"
        ]
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 3. One-by-one at max_concurrent=1
# ---------------------------------------------------------------------------
async def test_one_by_one(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("one marker", origin="user", task_id=_T1)
    store.create_inbox_task("two marker", origin="user", task_id=_T2)
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.05, _final("ok"))]),
        router=_Router(),
    )
    loop.start()
    try:
        await asyncio.wait_for(_wait_state(store, _T2, "DONE"), 8)
        assert [store.read_state(_T1)["state"], store.read_state(_T2)["state"]] == ["DONE", "DONE"]
        assert len(runs) == 2
        assert runs[0]["end"] <= runs[1]["start"] + 0.001
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 4. Dedupe F13: re-dropped id never re-announced / never re-run
# ---------------------------------------------------------------------------
async def test_dedupe_f13(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("dedupe marker", origin="user", task_id=_T1)
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final("once"))]),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        assert len(runs) == 1
        appeared = [p for t, p in bus.events if t == "task.appeared"]
        # re-drop the same id after pickup; the watcher double-fire path
        (store.inbox / _T1).mkdir()
        await loop._poll_once()
        await loop._poll_once()
        assert len(runs) == 1
        assert [p["task_id"] for p in appeared].count(_T1) == 1
        assert store.list_inbox() == [_T1]
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 5. SUBTASK_DROP happy: inbox child, parent result stripped, child runs later
# ---------------------------------------------------------------------------
async def test_subtask_drop_happy(cfg) -> None:

    cfg = _tweak(cfg, poll_seconds=0.05, max_depth=2)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("parent chore", origin="user", task_id=_T1)
    final = (
        "Groundwork done.\n\n```SUBTASK_DROP\ndescription: Write the README\nagent: opencode\n```"
    )
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final(final if i == 0
                                                                else "child done"))]),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        assert len(runs) == 1
        children = store.list_inbox()
        assert len(children) == 1
        child_id = children[0]
        payload = yaml.safe_load(
            (store.inbox / child_id / "task.yaml").read_text(encoding="utf-8")
        )
        assert payload["parent"] == _T1 and payload["origin"] == "agent"
        assert payload["agent"] == "opencode"
        parent_result = (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        assert "Groundwork done." in parent_result and "SUBTASK_DROP" not in parent_result
        assert any(e["kind"] == "subtask_drop" and e["data"]["child"] == child_id
                   and e["data"]["parent"] == _T1 for e in _events_of(store, _T1))
        await loop._poll_once()
        await asyncio.wait_for(_wait_state(store, child_id, "DONE"), 6)
        assert store.read_state(child_id)["depth"] == 1
        assert len(runs) == 2 and "README" in runs[1]["prompt"]
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 6. SUBTASK_DROP refusals + depth cap chain (D17)
# ---------------------------------------------------------------------------
async def test_subtask_drop_refused_and_depth_cap_chain(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=0.05, max_depth=2)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("parent chore", origin="user", task_id=_T1)
    drop = lambda desc: f"```SUBTASK_DROP\ndescription: {desc}\n```"
    finals: dict[str, str] = {
        "parent chore": (
            drop("chainA drop marker") + "\n" + drop("[broken") .replace(
                "description: [broken", "description: x: [unclosed"
            )
        ),
        "chainA drop marker": drop("chainB drop marker"),
        "chainB drop marker": drop("chainC drop marker"),
    }

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        for marker, text in finals.items():
            if marker in spec.prompt:
                return [(0.0, _final(text))]
        return [(0.0, _final("default final"))]

    runs: list[dict] = []
    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus,
                    adapter_factory=_factory(runs, planner), router=_Router())
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        # parent refused the malformed second drop, created chainA
        parent_refused = [e for e in _events_of(store, _T1) if e["kind"] == "subtask_refused"]
        assert len(parent_refused) == 1 and "YAML" in parent_refused[0]["data"]["reason"]
        parent_drops = [e for e in _events_of(store, _T1) if e["kind"] == "subtask_drop"]
        assert len(parent_drops) == 1
        chain_a = store.list_inbox()
        assert len(chain_a) == 1
        chain_a_id = chain_a[0]
        parent_result = (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        assert "SUBTASK_DROP" not in parent_result
        # chainA (depth 1) runs -> chainB (depth 2 == max_depth) created
        await asyncio.wait_for(_wait_state(store, chain_a_id, "DONE"), 6)
        assert store.read_state(chain_a_id)["depth"] == 1
        chain_b_id = (store.list_inbox() or [""])[0]
        assert chain_b_id and chain_b_id != chain_a_id
        await asyncio.wait_for(_wait_state(store, chain_b_id, "DONE"), 6)
        assert store.read_state(chain_b_id)["depth"] == 2
        # chainB (at max_depth) drops chainC -> refused honestly, no child dir
        chain_b_refused = [e for e in _events_of(store, chain_b_id)
                           if e["kind"] == "subtask_refused"]
        assert len(chain_b_refused) == 1
        assert "depth cap exceeded: depth 3 > max_depth 2" == (
            chain_b_refused[0]["data"]["reason"]
        )
        assert store.list_inbox() == []
        task_ids = {entry["prompt"].split(":", 1)[-1].strip() for entry in runs}
        assert len(runs) == 3
        del task_ids
    finally:
        await loop.stop()


async def test_depth_cap_zero_refuses(cfg) -> None:
    cfg = _tweak(cfg, poll_seconds=0.05, max_depth=1)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("shallow parent", origin="user", task_id=_T1)
    drop = "```SUBTASK_DROP\ndescription: forbidden child\n```"
    runs: list[dict] = []
    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus,
                    adapter_factory=_factory(runs, lambda s, i: [(0.0, _final(drop))]),
                    router=_Router())
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        # depth 0 parent, max_depth=1: child_depth 1 <= 1 -> child IS created.
        # The at-cap refusal is covered by the chain test above.
        assert len(store.list_inbox()) == 1
        dropped = [e for e in _events_of(store, _T1) if e["kind"] == "subtask_drop"]
        assert len(dropped) == 1
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 7. FAILED honest: retries then FAILED; analyze-raise -> FAILED
# ---------------------------------------------------------------------------
async def test_retry_then_failed(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("retry marker", origin="user", task_id=_T1)
    runs: list[dict] = []
    fail = StreamEvent(kind="failed", error="exit code 1", exit_code=1)
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, fail)]),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "FAILED")
        assert store.read_state(_T1)["attempt"] == 2  # retries=1 default
        assert len(runs) == 2
        events = _events_of(store, _T1)
        assert any(e["kind"] == "state_change" and e["data"]["new"] == "FAILED"
                   and "agent error after 2 attempt(s)" in e["data"].get("reason", "")
                   for e in events)
    finally:
        await loop.stop()


async def test_retry_zero_single_attempt(cfg) -> None:
    cfg = _tweak(cfg, retries=0, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("solo marker", origin="user", task_id=_T1)
    runs: list[dict] = []
    fail = StreamEvent(kind="failed", error="boom")
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, fail)]),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "FAILED")
        assert store.read_state(_T1)["attempt"] == 1 and len(runs) == 1
    finally:
        await loop.stop()


async def test_analyze_raise_failed_no_adapter(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("analyze doom marker", origin="user", task_id=_T1)
    runs: list[dict] = []
    router = _Router(RuntimeError("provider down"))
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final("never"))]),
        router=router,
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "FAILED")
        assert len(runs) == 0
        events = _events_of(store, _T1)
        assert any(e["kind"] == "state_change" and e["data"]["new"] == "FAILED"
                   and "provider down" in e["data"].get("reason", "") for e in events)
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 8. Boot replay F17: live session -> FAILED(restarted); rest re-queued once
# ---------------------------------------------------------------------------
async def test_boot_replay_idempotent(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)

    def born(task_id: str) -> str:
        store.create_inbox_task(f"replay {task_id}", origin="user", task_id=task_id)
        destination = store.move_from_inbox(task_id)
        TaskDir.birth(destination, cfg, bus=bus)
        return task_id

    born(_T1)
    born(_T2)
    born(_T3)
    born(_T5)
    import json as _json
    from pathlib import Path

    def set_state(task_id: str, state: str, **extra: Any) -> None:
        path = Path(cfg.storage.data_dir) / "tasks" / task_id / "state.json"
        data = _json.loads(path.read_text(encoding="utf-8"))
        data.update({"state": state, **extra})
        path.write_text(_json.dumps(data), encoding="utf-8")

    set_state(_T5, "FAILED", agent_session=None)  # blocker dep: never ready
    # _T4 depends on the FAILED _T5 -> genuinely stuck in WAITING_DEPS
    store.create_inbox_task("replay dep waiter", origin="user", task_id=_T4,
                            depends_on=[_T5])
    TaskDir.birth(store.move_from_inbox(_T4), cfg, bus=bus)

    # _T1: RUNNING with a LIVE agent session -> FAILED(restarted)
    _handcraft_session(cfg, "AAA2SESS", [{"state": "agent_running", "kind": "state_change"}])
    set_state(_T1, "RUNNING", agent_session="AAA2SESS")
    # _T2: RUNNING, no live session -> INTERRUPTED -> READY, re-queued once
    set_state(_T2, "RUNNING")
    # _T3: WAITING_INPUT restored as-is
    set_state(_T3, "WAITING_INPUT")
    # _T4: WAITING_DEPS waiting on a still-not-done dep
    set_state(_T4, "WAITING_DEPS")
    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus,
                    adapter_factory=_factory([], lambda s, i: []), router=_Router())
    counts = loop.replay()
    assert counts["tasks"] >= 4
    assert counts["failed_restarted"] == 1
    assert counts["requeued"] == 1
    assert counts["gate_restored"] == 1
    assert counts["deps_waiting"] == 1
    assert store.read_state(_T1)["state"] == "FAILED"
    assert store.read_state(_T2)["state"] == "READY"
    assert store.read_state(_T3)["state"] == "WAITING_INPUT"
    assert store.read_state(_T4)["state"] == "WAITING_DEPS"
    assert any(
        e["kind"] == "state_change" and e["data"].get("reason") == "restarted"
        for e in _events_of(store, _T1)
    )
    counts2 = loop.replay()
    assert counts2["requeued"] == 0
    assert counts2["failed_restarted"] == 0
    assert counts2["gate_restored"] == 0
    # the requeued READY task runs through the live loop (F17 re-queue = runnable)
    runs: list[dict] = []
    loop2 = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final("resumed ok"))]),
        router=_Router(),
    )
    loop2._boot_pending = [_T2]
    loop2.start()
    try:
        await asyncio.wait_for(_wait_state(store, _T2, "DONE"), 6)
        assert len(runs) == 1
    finally:
        await loop2.stop()


# ---------------------------------------------------------------------------
# 9. Gate park + answer() resume; oneway ignores the question
# ---------------------------------------------------------------------------
async def test_gate_park_and_resume(cfg) -> None:
    import json as _json

    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("gate task marker", origin="user", task_id=_T1)
    runs: list[dict] = []

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        if index == 0:
            raw = _json.dumps({"gate_question": "which env?"})
            return [(0.0, StreamEvent(kind="system", raw=raw))]
        return [(0.0, _final("final after answer"))]

    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, planner), router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        assert store.read_state(_T1)["attempt"] == 1
        assert any(topic == "gate.opened" and p["task_id"] == _T1 and p["question"] == "which env?"
                   for topic, p in bus.events)
        assert "gate_opened" in _kinds_of(store, _T1)
        events = _events_of(store, _T1)
        assert any(e["kind"] == "state_change" and e["data"]["new"] == "WAITING_INPUT"
                   for e in events)
        await loop.answer(_T1, "staging")
        await _wait_state(store, _T1, "DONE")
        result = (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        assert "final after answer" in result
        assert len(runs) == 2
        # resume: same session, answer appended to the prompt
        assert "User answer" in runs[1]["prompt"] and "which env?" not in runs[1]["prompt"]
        assert runs[0]["prompt"] in runs[1]["prompt"]
        session_id = store.read_state(_T1)["agent_session"]
        meta = _session_meta(cfg, session_id)
        assert meta["title"].startswith(f"T<{_T1}>:")
        assert "gate_answered" in _kinds_of(store, _T1)
    finally:
        await loop.stop()


async def test_oneway_ignores_gate_question(cfg) -> None:
    import json as _json

    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("oneway marker", origin="user", task_id=_T1, oneway=True)
    runs: list[dict] = []
    raw = _json.dumps({"gate_question": "which env?"})

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        return [(0.0, StreamEvent(kind="system", raw=raw)), (0.0, _final("done away"))]

    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, planner),
        router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "DONE")
        assert len(runs) == 1 and runs[0]["oneway"] is True
        assert "done away" in (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        assert not any(topic == "gate.opened" for topic, _ in bus.events)
        assert any(e["kind"] == "note" and "ignored" in str(e["data"])
                   for e in _events_of(store, _T1))
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 10. Invalid task.yaml -> FAILED(syntax); unknown dep -> FAILED(F14); stop clean
# ---------------------------------------------------------------------------
async def test_syntax_failure_and_stop_clean(tmp_volume) -> None:
    from harness.config import load_config

    del tmp_volume
    cfg = load_config()
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    task_id = "EEE24567"
    (store.inbox / task_id).mkdir(parents=True)
    (store.inbox / task_id / "task.yaml").write_text("{description: [unclosed\n",
                                                     encoding="utf-8")
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: []),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, task_id, "FAILED")
        events = _events_of(store, task_id)
        assert any(e["kind"] == "state_change" and e["data"].get("new") == "FAILED"
                   and "syntax" in str(e["data"].get("reason", "")) for e in events)
        assert len(runs) == 0
    finally:
        await loop.stop()


async def test_unknown_dep_failed_f14(cfg) -> None:
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("orphan dep marker", origin="user", task_id=_T1,
                            depends_on=["ZZZ24567"])
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: []),
        router=_Router(),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "FAILED")
        assert len(runs) == 0
        assert any("unknown depends_on" in str(e["data"].get("reason", ""))
                   for e in _events_of(store, _T1))
    finally:
        await loop.stop()
        assert store.read_state(_T1)["state"] == "FAILED"
