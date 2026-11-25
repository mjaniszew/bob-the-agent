"""TDD failing-first tests for P8 — taskloop/scheduler bus topics + CapabilityEvent
bridge (D3 ask_user visibility, plan §P8, docs/plans/10-pydantic-ai-v2-migration-plan.md
lines 439-454). Pins the REAL pydantic-ai 2.73.0 CapabilityEvent surfaces (live probe:
``pydantic_ai_harness.ask_user.AskUserRequestedEvent`` / ``AskUserAnsweredEvent``,
``CapabilityEvent`` base fields ``kind``/``capability_id``/``tool_call_id``/
``tool_name`` + ``event_kind='capability'``, kinds ``ask_user.requested`` /
``ask_user.answered``):

1.  :func:`test_ask_user_requested_event_publishes` — an ``AskUserRequestedEvent``
    maps to bus topic ``harness.ask_user.requested`` (payload-key tuple documented
    in bus.py TOPICS; value restrictions stay as-is).
2.  :func:`test_ask_user_answered_event_publishes` — an ``AskUserAnsweredEvent``
    maps to ``harness.ask_user.answered`` carrying ``tool_call_id`` + ``task_id``
    (the deferred gate resume path).
3.  :func:`test_events_jsonl_registry_untouched` — the closed events.jsonl kinds
    registry (``bob_harness.tasks.EVENTS``) gains NO new kinds (topic additions
    only, the plan-registered exception on the BUS registry).
4.  :func:`test_scheduler_spawn_through_taskloop` — a scheduler-born task keeps
    the unchanged spawn->inbox->single-runner pickup chain under the P7
    executor-runner default.
5.  :func:`test_scheduler_restore_with_exec_runner` — the boot ``restore()``
    policy (within-grace re-fires, beyond-grace ``misfire_skips``) stays green
    with ``tasks.runner="executor"``.
6.  :func:`test_unknown_capability_events_ignored` — the bridge mapper tolerates
    unmapped CapabilityEvent kinds (None, no exception, no publish).

Topic additions: ``harness.ask_user.requested`` and ``harness.ask_user.answered``
(payload keys pinned by the TOPICS tuples: ``task_id``, ``tool_call_id``,
``kind``, ``event_kind``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic_ai.messages import (
    CapabilityEvent,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai_harness.ask_user import (
    AskUserAnsweredEvent,
    AskUserRequestedEvent,
)

from bob_harness.adapters.base import RunSpec, StreamEvent
from bob_harness.bus import TOPICS
from bob_harness.scheduler import SchedulerEngine
from bob_harness.session_manager import SessionManager
from bob_harness.taskloop import TaskLoop
from bob_harness.tasks import EVENTS, TaskStore

_T1 = "ADA23456"

_TC_ID = "tc-p8-1"

_QUESTIONS = [
    {
        "header": "env",
        "question": "which env?",
        "options": [
            {"label": "prod", "description": "production env"},
            {"label": "staging", "description": "staging env"},
        ],
        "multi_select": False,
    }
]


# ---------------------------------------------------------------------------
# Helpers (local clones; no cross-test imports — delegated-implementation rule)
# ---------------------------------------------------------------------------
class _RecordingBus:
    """Minimal duck-typed bus (tests/test_taskloop.py _Bus shape)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1

    def topics(self, name: str) -> list[dict]:
        return [dict(payload) for topic, payload in self.events if topic == name]


class _Router:
    def __init__(self, result: Any) -> None:
        self.result = result

    def analyze(self, task_desc: str, *, workdir: str | None = None) -> Any:
        return self.result


class _Adapter:
    """Scripted adapter: planner(spec, index) -> list[(delay, StreamEvent)]."""

    def __init__(self, runs: list[dict], planner: Any) -> None:
        self._runs = runs
        self._planner = planner
        self._count = 0

    def run(self, spec: RunSpec) -> Any:
        self._count += 1
        self._runs.append({"prompt": spec.prompt, "resume_ref": spec.resume_ref})

        async def gen() -> Any:
            for _delay, event in self._planner(spec, self._count - 1):
                yield event

        return gen()


def _factory(runs: list[dict], planner: Any) -> Any:
    def make(agent: str) -> _Adapter:
        return _Adapter(runs, planner)

    return make


def _final(text: str, **kwargs: Any) -> StreamEvent:
    return StreamEvent(kind="final", final_text=text, **kwargs)


def _analysis(seed: str, *, agent: str = "hermes", mode: str = "oneway") -> Any:
    from bob_harness.router import Analysis

    return Analysis(
        agent=agent,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        workdir="/app/projects",
        prompt_seed=seed,
        task_title=seed[:40],
    )


async def _wait_state(store: TaskStore, task_id: str, want: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"task {task_id} never reached {want}; got {store.read_state(task_id)}")


def _deferred_system_event() -> StreamEvent:
    """The executor park payload (deferred seed + parked history JSON str),
    P6 fixture shape (tests/test_gate_deferred.py, local clone)."""

    call_part = ToolCallPart(
        tool_name="ask_user_question",
        args={"questions": _QUESTIONS},
        tool_call_id=_TC_ID,
    )
    messages_json = ModelMessagesTypeAdapter.dump_json(
        [
            ModelResponse(
                parts=[call_part],
                timestamp=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
            )
        ]
    ).decode()
    seed = {
        "calls": [dataclasses.asdict(call_part)],
        "approvals": [],
        "metadata": {
            _TC_ID: {
                "ask_user": "ask_user",
                "questions": _QUESTIONS,
                "request_id": _TC_ID,
            }
        },
        "validated_args": {_TC_ID: {"questions": _QUESTIONS}},
        "_kinds": {_TC_ID: "question"},
    }
    return StreamEvent(kind="system", raw=json.dumps({"deferred": seed, "messages": messages_json}))


def _park_deferred(cfg: Any, bus: Any, store: TaskStore, runs: list[dict]) -> TaskLoop:
    """Build a loop that parks _T1 on one deferred ask_user call (test-8 shape
    of tests/test_gate_deferred.py); caller runs `.start()`."""
    cfg = cfg.model_copy(update={"tasks": cfg.tasks.model_copy(update={"poll_seconds": 0.05})})
    store.create_inbox_task("deferred bridge park", origin="user", task_id=_T1)
    return TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(
            runs,
            lambda spec, index: (
                [(0.0, _deferred_system_event())] if index == 0 else [(0.0, _final("final after answer"))]
            ),
        ),
        router=_Router(_analysis("bridge park", mode="gate")),
    )


# ---------------------------------------------------------------------------
# 1 + 2. The bridge: AskUser CapabilityEvents -> bus topics.
# ---------------------------------------------------------------------------


async def test_ask_user_requested_event_publishes(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume  # tmp_volume keeps the data_dir isolated via the cfg fixture
    # The REAL CapabilityEvent subclasses on harness 0.73.0 (live-probed).
    assert issubclass(AskUserRequestedEvent, CapabilityEvent)
    assert issubclass(AskUserAnsweredEvent, CapabilityEvent)
    base = {f.name for f in dataclasses.fields(CapabilityEvent)}
    assert {"kind", "capability_id", "tool_call_id", "tool_name"} <= base
    requested = AskUserRequestedEvent(
        kind="ask_user.requested",
        tool_call_id=_TC_ID,
        tool_name="ask_user_question",
        request=dataclasses_fields_request(),
    )
    assert requested.kind == "ask_user.requested"
    assert requested.tool_call_id == _TC_ID

    bus = _RecordingBus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    cfg = cfg.model_copy(update={"tasks": cfg.tasks.model_copy(update={"poll_seconds": 0.05})})
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(
            runs,
            lambda spec, index: (
                [(0.0, _deferred_system_event())] if index == 0 else [(0.0, _final("final"))]
            ),
        ),
        router=_Router(_analysis("ask me", mode="gate")),
    )
    store.create_inbox_task("ask me a question", origin="user", task_id=_T1)
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
    finally:
        await loop.stop()
    published = bus.topics("harness.ask_user.requested")
    assert published, ["harness.ask_user.requested never published", bus.events]
    payload = published[-1]
    assert payload["task_id"] == _T1
    assert payload["tool_call_id"] == _TC_ID
    assert payload["kind"] == "ask_user.requested"
    assert payload["event_kind"] == "capability"


def dataclasses_fields_request() -> Any:
    """One AskUserRequest built the way the toolset builds it (id = tool_call_id).
    A tool question rides the validated args; the request pins the id seam."""
    from pydantic_ai_harness.ask_user import AskUserRequest, Question, QuestionOption

    return AskUserRequest(
        questions=(
            Question(
                header=_QUESTIONS[0]["header"],
                question=_QUESTIONS[0]["question"],
                options=tuple(
                    QuestionOption(label=o["label"], description=o["description"])
                    for o in _QUESTIONS[0]["options"]
                ),
            ),
        ),
        id=_TC_ID,
    )


async def test_ask_user_answered_event_publishes(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    bus = _RecordingBus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    loop = _park_deferred(cfg, bus, store, runs)
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        # The REAL AnsweredEvent the resumed executor run emits (live probe:
        # request_id = the AskUserRequest id, response = AskUserResponse).
        from pydantic_ai_harness.ask_user._types import AskUserAnswer, AskUserResponse

        answered = AskUserAnsweredEvent(
            kind="ask_user.answered",
            tool_call_id=_TC_ID,
            tool_name="ask_user_question",
            request_id=_TC_ID,
            response=AskUserResponse(
                answers=(AskUserAnswer(header="env", selected=["staging"]),)
            ),
        )
        assert answered.kind == "ask_user.answered"
        assert answered.timed_out is False and answered.failed is False
        # The bridge publishes the bus topic on the deferred gate resume.
        await loop.answer(_T1, "staging")
        await _wait_state(store, _T1, "DONE")
    finally:
        await loop.stop()
    published = bus.topics("harness.ask_user.answered")
    assert published, ["harness.ask_user.answered never published", bus.events]
    payload = published[-1]
    assert payload["task_id"] == _T1
    assert payload["tool_call_id"] == _TC_ID
    assert payload["kind"] == "ask_user.answered"


# ---------------------------------------------------------------------------
# 3. The events.jsonl kinds registry stays CLOSED (topic additions only).
# ---------------------------------------------------------------------------


def test_events_jsonl_registry_untouched() -> None:
    assert EVENTS == {
        "appeared",
        "state_change",
        "gate_opened",
        "gate_answered",
        "note",
        "subtask_drop",
        "subtask_refused",
        "subtask_schedule_note",
        "subtask_schedule_accepted",
        "dep_wait",
        "result_written",
    }
    assert len(EVENTS) == 11
    # The BUS registry however gains the two ask_user topics (plan exception:
    # topic additions only; the events.jsonl kinds registry stays closed).
    assert "harness.ask_user.requested" in TOPICS
    assert "harness.ask_user.answered" in TOPICS
    assert TOPICS["harness.ask_user.requested"] == ("task_id", "tool_call_id", "kind", "event_kind")
    assert TOPICS["harness.ask_user.answered"] == ("task_id", "tool_call_id", "kind", "event_kind")
    # The documented value restrictions on the CLOSED topics are untouched.
    assert "gate.notify" in TOPICS and "reply.chunk" in TOPICS
    assert len(TOPICS) == 19


# ---------------------------------------------------------------------------
# 4. Scheduler spawn -> inbox task -> single-runner pickup (unchanged, with
# the executor runner default P7 flipped).
# ---------------------------------------------------------------------------


async def test_scheduler_spawn_through_taskloop(
    tmp_volume: Any, cfg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_volume
    cfg = cfg.model_copy(update={"tasks": cfg.tasks.model_copy(update={"poll_seconds": 0.05})})
    assert cfg.tasks.runner == "executor"  # the P7 default stands
    engine = SchedulerEngine(cfg, bus=None)
    boot = datetime.now(UTC)
    await engine.add_job(
        {
            "id": "p8-spawn",
            "kind": "drop_task",
            "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
            "description": "scheduled drop through the loop",
        },
        persist=False,
    )
    await engine.start()
    store = TaskStore(cfg)
    spawned: str | None = None
    for _ in range(100):
        inbox = store.list_inbox()
        if inbox:
            spawned = inbox[0]
            break
        await asyncio.sleep(0.05)
    await engine.stop()
    assert spawned is not None
    # Pickup simulation replaced: the task's provenance must say origin=-
    # "scheduler" while it is IN the inbox (the birth at real pickup inherits
    # it via taskloop's _read_provenance); pin it on the inbox task.yaml.
    from bob_harness.tasks import load_task_yaml

    spawned_yaml = load_task_yaml(store.inbox / spawned / "task.yaml")
    assert spawned_yaml["origin"] == "scheduler"
    # The loop's SINGLE runner picks the born task up and completes it.
    bus = _RecordingBus()
    runs: list[dict] = []
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, _final("spawned work done."))]),
        router=_Router(_analysis("spawned chore")),
    )
    loop.start()
    try:
        await _wait_state(store, spawned, "DONE")
        assert len(runs) == 1
        # task.appeared published exactly once (F13) for the scheduled spawn.
        assert len([t for t, _p in bus.events if t == "task.appeared"]) == 1
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 5. Restore path green under the executor-runner default.
# ---------------------------------------------------------------------------


async def test_scheduler_restore_with_exec_runner(
    tmp_volume: Any, cfg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_volume
    cfg = cfg.model_copy(
        update={"tasks": cfg.tasks.model_copy(update={"runner": "executor", "poll_seconds": 0.05})}
    )
    assert cfg.tasks.runner == "executor"
    engine = SchedulerEngine(cfg, bus=None)
    boot = datetime.now(UTC)
    within = {
        "id": "p8-restore-within",
        "kind": "drop_task",
        "schedule": {"type": "at", "iso": (boot + timedelta(seconds=60)).isoformat()},
        "description": "future at within grace",
    }
    beyond = {
        "id": "p8-restore-beyond",
        "kind": "drop_task",
        "schedule": {"type": "at", "iso": (boot - timedelta(seconds=3600)).isoformat()},
        "description": "ancient at beyond grace",
    }
    for spec in (within, beyond):
        await engine.add_job(spec, persist=True)
    await engine.stop()
    engine2 = SchedulerEngine(cfg, bus=None)
    restored = await engine2.restore()
    assert restored["restored"] == 1
    assert restored["misfire_skipped"] == 1
    assert [job["id"] for job in engine2.list_jobs()] == ["p8-restore-within"]
    await engine2.stop()


# ---------------------------------------------------------------------------
# 6. The bridge tolerates unmapped CapabilityEvent kinds (subscriber safety).
# ---------------------------------------------------------------------------


async def test_unknown_capability_events_ignored(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume

    @dataclasses.dataclass(kw_only=True)
    class WeirdEvent(CapabilityEvent, namespace="weird_ns"):
        payload: dict = dataclasses.field(default_factory=dict)

    weird = WeirdEvent()  # the registration injects kind='weird_ns.weird'
    # The bridge mapper (taskloop sink) returns None for unmapped kinds.
    from bob_harness.taskloop import capability_event_to_topic

    assert capability_event_to_topic(weird, task_id=_T1) is None
    # Known kinds DO map.
    requested = AskUserRequestedEvent(kind="ask_user.requested", tool_call_id=_TC_ID)
    mapped = capability_event_to_topic(requested, task_id=_T1)
    assert mapped == (
        "harness.ask_user.requested",
        {"task_id": _T1, "tool_call_id": _TC_ID,
         "kind": "ask_user.requested", "event_kind": "capability"},
    )
    # And a REAL bus accepts the two new topics end-to-end (subscriber safety).
    from bob_harness.bus import Bus as RealBus

    real = RealBus()
    queue = real.subscribe("p8-observer")
    assert await real.publish("harness.ask_user.requested", dict(mapped[1])) == 1
    topic, payload = queue.get_nowait()
    assert topic == "harness.ask_user.requested"
    assert payload["task_id"] == _T1
