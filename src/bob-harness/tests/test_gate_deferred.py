"""TDD failing-first tests for P6 — WAITING_INPUT deferred flow (D3, plan §P6).

Pins the pydantic-ai v2 deferred-tool gate surfaces the executor + TaskLoop
must expose (plan Part 2 SS4 P6 block, docs/plans/10-pydantic-ai-v2-migration-plan.md
lines 402-419):

1.  :func:`test_executor_question_returns_deferred_requests` — the executor's
    ``AskUser`` (answerer=None, the D3 deferred wiring) turns a model tool call
    into ONE ``system`` stream event carrying the ``DeferredToolRequests`` seed
    + the exact in-run message history, NO terminal event (the run parks).
2.  :func:`test_taskloop_parks_on_deferred` — the deferred system event drives
    ``RUNNING → WAITING_INPUT`` + a gates.jsonl row + ``deferred_messages.json``
    + ``deferred_seed.json`` in the task folder (park persistence).
3.  :func:`test_gate_timer_renotify_then_failed` — the F15 timer contract is
    byte-unchanged on a deferred gate (24h default, exactly one ``gate.notify``
    re-notify, FAILED(gate_timeout), timeout row) and the timeout path CLEARS
    the deferred seed files.
4.  :func:`test_answer_resumes_with_deferred_results` — ``answer()`` builds the
    ``DeferredToolResults`` resume (calls={tool_call_id: value}), the ORIGINAL
    in-run history rides the resume, the run completes, and the session
    transcript honestly records the user answer.
5.  :func:`test_parked_task_survives_restart` — kill/re-create the TaskLoop,
    ``replay()`` restores the gate row, re-arms the timer and RELOADS the seed
    from disk; the resume runs with the parked all_messages_json round-trip.
6.  :func:`test_subprocess_adapters_keep_prompt_append_resume` — hermes/fake
    question parks keep the EXACT prompt-append resume (no deferred machinery,
    no seed files).
7.  :func:`test_gateway_answer_endpoint_unchanged` — ``POST /api/tasks/answer
    /{task_id}`` drives the SAME answer() resume path on a deferred park.
8.  :func:`test_discord_gate_answered_consumed` — the ``gate.answered`` bus
    topic drives the same answer() on a deferred park.

OQ7/OQ5 decisions embedded: the seed/messages round-trip is verified against
the REAL pydantic-ai types (``DeferredToolRequests`` asdict ⇄ rebuild,
``ModelMessagesTypeAdapter.validate_json``) in tests 1/4/5.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from bob_harness.adapters.base import RunSpec, StreamEvent
from bob_harness.bus import Bus as _RealBus
from bob_harness.gateway.app import create_app
from bob_harness.session_manager import SessionManager
from bob_harness.taskloop import TaskLoop
from bob_harness.tasks import TaskStore
from tests.test_taskloop import (
    _T1,
    _analysis,
    _Bus,
    _events_of,
    _final,
    _kinds_of,
    _Router,
    _session_meta,
    _tweak,
    _wait_state,
)

_TOOL_NAME = "ask_user_question"
_SEED_FILES = ["deferred_messages.json", "deferred_seed.json"]
_QUESTION_TEXT = "which env? and how?"
_HEADERS_ENV = "staging"


class _Question:
    """The deferred ask_user question fixture (validated tool-args shape)."""

    def to_dict(self) -> dict:
        return {
            "header": "env",
            "question": _QUESTION_TEXT,
            "options": [
                {"label": "prod", "description": "production env"},
                {"label": _HEADERS_ENV, "description": "staging env"},
            ],
            "multi_select": False,
        }


QUESTION = _Question()
ANSWER = _HEADERS_ENV
TC_ID = "tc-defer-1"


def _seed_args() -> dict:
    return {"questions": [QUESTION.to_dict()]}


def _defer_metadata() -> dict:
    return {
        "ask_user": "ask_user",
        "questions": [QUESTION.to_dict()],
        "request_id": TC_ID,
    }


def _call_part_dict() -> dict:
    """One ToolCallPart as a plain dict (dataclasses.asdict shape)."""
    part = ToolCallPart(tool_name=_TOOL_NAME, args=_seed_args(), tool_call_id=TC_ID)
    return dataclasses.asdict(part)


def _defer_messages_json() -> str:
    """The parked in-run history: ModelResponse(ToolCallPart) -> JSON str.
    The ModelResponse timestamp is PINNED so two generations are byte-equal —
    the parked document and a fresh generation must compare equal."""
    response = ModelResponse(
        parts=[ToolCallPart(tool_name=_TOOL_NAME, args=_seed_args(),
                             tool_call_id=TC_ID)],
        timestamp=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    return ModelMessagesTypeAdapter.dump_json([response]).decode()


def _deferred_system_event(messages_json: str) -> StreamEvent:
    """The executor park payload: ONE system event, raw = JSON str."""
    return StreamEvent(
        kind="system",
        raw=json.dumps({"deferred": _defer_seed_dict(), "messages": messages_json}),
    )


def _defer_seed_dict() -> dict:
    return {
        "calls": [_call_part_dict()],
        "approvals": [],
        "metadata": {TC_ID: _defer_metadata()},
        "validated_args": {TC_ID: _seed_args()},
        "_kinds": {TC_ID: "question"},
    }


# ---------------------------------------------------------------------------
# Local scripted adapter: records the full RunSpec (incl. dynamic
# ``deferred_resume``) + streams a planner-scripted event list per attempt.
# ---------------------------------------------------------------------------
class _RecordingAdapter:
    """Scripted adapter recording each RunSpec verbatim (test seam)."""

    def __init__(self, runs: list[dict], planner: Any) -> None:
        self._runs = runs
        self._planner = planner
        self._count = 0

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self._count += 1
        entry: dict = {
            "prompt": spec.prompt,
            "resume_ref": spec.resume_ref,
            "profile": spec.profile,
            "workdir": spec.workdir,
            "oneway": spec.oneway,
            "deferred_resume": getattr(spec, "deferred_resume", None),
        }
        self._runs.append(entry)

        async def gen() -> AsyncIterator[StreamEvent]:
            for _delay, event in self._planner(spec, self._count - 1):
                yield event

        return gen()


def _recording_factory(runs: list[dict], planner: Any):
    def make(agent: str) -> _RecordingAdapter:
        return _RecordingAdapter(runs, planner)

    return make


def _read_seed(store: Any, task_id: str, name: str) -> Any:
    return json.loads((store.root / task_id / name).read_text(encoding="utf-8"))


def _gate_rows(cfg: Any) -> list[dict]:
    from bob_harness.persist import read_jsonl

    return read_jsonl(Path(cfg.storage.data_dir) / "state" / "gates.jsonl")


def _transcript_records(cfg: Any, session_id: str) -> list[dict]:
    from bob_harness.persist import read_jsonl

    return read_jsonl(
        Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl"
    )


def _deferred_planner(messages_json: str, final_text: str):
    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        # A deferred RESUME carries spec.deferred_resume — it completes with
        # the final regardless of which adapter instance runs it (test 5's
        # restart loop is a FRESH adapter whose first call IS the resume).
        if getattr(spec, "deferred_resume", None) is not None:
            return [(0.0, _final(final_text))]
        if index == 0:
            return [(0.0, _deferred_system_event(messages_json))]
        return [(0.0, _final(final_text))]

    return planner


async def _park_deferred(cfg: Any, bus: Any, store: TaskStore,
                         runs: list[dict], *, final_text: str = "final after deferred answer",
                         messages_json: str | None = None) -> TaskLoop:
    """Start a loop that parks _T1 on one deferred ask_user call."""
    if messages_json is None:
        messages_json = _defer_messages_json()
    store.create_inbox_task("deferred park marker", origin="user", task_id=_T1)
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_recording_factory(runs, _deferred_planner(messages_json, final_text)),
        router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    await _wait_state(store, _T1, "WAITING_INPUT")
    return loop


def _fake_brain(cfg: Any) -> Any:
    from bob_harness.router import RouterBrain
    from tests.mocks.router_model import make_fake_model

    return RouterBrain(cfg, model=make_fake_model([{"agent": "hermes", "task_title": "demo"}]))


# ---------------------------------------------------------------------------
# 1. Executor: AskUser question -> DeferredToolRequests system event (park);
#    the verified pydantic-ai round-trip seam.
# ---------------------------------------------------------------------------
async def test_executor_question_returns_deferred_requests(
    tmp_volume: Any, cfg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_volume

    def defer_only(messages: Any = None, info: Any = None) -> Any:
        del messages, info
        return ModelResponse(parts=[ToolCallPart(tool_name=_TOOL_NAME, args=_seed_args(),
                                                 tool_call_id=TC_ID)])

    import bob_harness.adapters.executor as ex

    monkeypatch.setattr(ex, "_model_from_config", lambda config: FunctionModel(defer_only))
    from bob_harness.adapters.executor import ExecutorAdapter

    adapter = ExecutorAdapter(cfg)
    events: list[StreamEvent] = [event async for event in adapter.run(RunSpec(prompt="which env?"))]
    kinds = [event.kind for event in events]
    assert kinds == ["started", "system"], kinds
    raw = events[-1].raw
    assert isinstance(raw, str)
    payload = json.loads(raw)
    assert sorted(payload) == ["deferred", "messages"]
    seed = payload["deferred"]
    assert [call["tool_call_id"] for call in seed["calls"]] == [TC_ID]
    assert seed["_kinds"] == {TC_ID: "question"}
    assert seed["metadata"][TC_ID]["ask_user"] == "ask_user"
    assert seed["validated_args"][TC_ID]["questions"] == [QUESTION.to_dict()]
    # NO terminal event: the stream suspends and the run parks.
    assert "final" not in kinds and "failed" not in kinds
    # Verified round-trip seam: rebuild the requests + build the results.
    rebuilt = DeferredToolRequests(
        calls=[ToolCallPart(**{k: v for k, v in d.items()}) for d in seed["calls"]],
        approvals=[ToolCallPart(**{k: v for k, v in d.items()}) for d in seed["approvals"]],
        metadata=seed["metadata"],
        validated_args=seed["validated_args"],
        _kinds=seed["_kinds"],
    )
    assert rebuilt.kind(TC_ID) == "question"
    results = rebuilt.build_results(calls={TC_ID: ANSWER})
    assert isinstance(results, DeferredToolResults)
    assert results.calls == {TC_ID: ANSWER}
    # The parked in-run history survives a JSON round-trip verbatim.
    msgs = ModelMessagesTypeAdapter.validate_json(payload["messages"])
    assert ModelMessagesTypeAdapter.dump_json(msgs) == payload["messages"].encode()
    assert any(
        getattr(part, "part_kind", None) == "tool-call"
        and getattr(part, "tool_call_id", None) == TC_ID
        for message in msgs
        for part in getattr(message, "parts", [])
    )


# ---------------------------------------------------------------------------
# 2. TaskLoop parks on the deferred system event: FSM + gates.jsonl + files.
# ---------------------------------------------------------------------------
async def test_taskloop_parks_on_deferred(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    loop = await _park_deferred(cfg, bus, store, runs)
    try:
        assert store.read_state(_T1)["state"] == "WAITING_INPUT"
        assert store.read_state(_T1)["attempt"] == 1
        # FSM path: RUNNING -> WAITING_INPUT (state_change rows, unchanged).
        events = _events_of(store, _T1)
        assert any(
            event["kind"] == "state_change" and event["data"]["new"] == "WAITING_INPUT"
            for event in events
        )
        # gate_opened event + bus topic, rendered question, unchanged shapes.
        assert _kinds_of(store, _T1).count("gate_opened") == 1
        opened = [e for e in events if e["kind"] == "gate_opened"]
        assert _QUESTION_TEXT in opened[-1]["data"]["question"]
        assert any(
            topic == "gate.opened" and payload["task_id"] == _T1
            and _QUESTION_TEXT in payload["question"]
            for topic, payload in bus.events
        )
        # Park persists both seed files in the task folder (json-read).
        messages_seed = _read_seed(store, _T1, "deferred_messages.json")
        assert messages_seed == _defer_messages_json()
        seed = _read_seed(store, _T1, "deferred_seed.json")
        assert [call["tool_call_id"] for call in seed["calls"]] == [TC_ID]
        assert seed["_kinds"] == {TC_ID: "question"}
        # gates.jsonl 'opened' audit row with the deadline (unchanged shape).
        rows = _gate_rows(cfg)
        opened_rows = [r for r in rows if r["task_id"] == _T1 and r["event"] == "opened"]
        assert opened_rows and opened_rows[-1]["deadline_ts"]
        assert set(opened_rows[-1]) == {"ts", "task_id", "event", "deadline_ts"}
        # The parked session exists with the task title (unchanged session flow).
        session_id = store.read_state(_T1)["agent_session"]
        meta = _session_meta(cfg, session_id)
        assert meta["title"].startswith(f"T<{_T1}>:")
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 3. F15 timer contract on a DEFERRED park: one re-notify, FAILED, seed clear.
# ---------------------------------------------------------------------------
async def test_gate_timer_renotify_then_failed(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05, gate_timeout_s=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    loop = await _park_deferred(cfg, bus, store, runs)
    assert (store.root / _T1 / "deferred_messages.json").is_file()
    try:
        await asyncio.sleep(0.15)  # the REAL 0.05 timer window + slack
        # Exactly one re-notify, then FAILED(gate_timeout) — bytes unchanged.
        notifies = [payload for topic, payload in bus.events if topic == "gate.notify"]
        assert len(notifies) == 1
        assert notifies[0]["task_id"] == _T1 and notifies[0]["kind"] == "timeout"
        assert _QUESTION_TEXT in notifies[0]["question"]
        assert store.read_state(_T1)["state"] == "FAILED"
        events = _events_of(store, _T1)
        assert any(
            event["kind"] == "state_change" and event["data"]["new"] == "FAILED"
            and event["data"].get("reason") == "gate_timeout"
            for event in events
        )
        # The timeout path CLEARS the deferred seed (impl step 4).
        assert not (store.root / _T1 / "deferred_messages.json").exists()
        assert not (store.root / _T1 / "deferred_seed.json").exists()
        # gates.jsonl: opened then timeout row (unchanged shapes).
        rows = [r["event"] for r in _gate_rows(cfg) if r["task_id"] == _T1]
        assert rows == ["opened", "timeout"]
        await asyncio.sleep(0.15)  # a second window: no double-notify
        assert len([p for topic, p in bus.events if topic == "gate.notify"]) == 1
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 4. answer() builds DeferredToolResults + resumes; session transcript honest.
# ---------------------------------------------------------------------------
async def test_answer_resumes_with_deferred_results(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    messages_json = _defer_messages_json()
    loop = await _park_deferred(cfg, bus, store, runs, messages_json=messages_json)
    try:
        await loop.answer(_T1, ANSWER)
        await _wait_state(store, _T1, "DONE")
        assert "final after deferred answer" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        assert len(runs) == 2
        # The resume ran ON the original in-run history (all_messages round-trip)
        # with the answer delivered via DeferredToolResults.
        resumed = runs[1]["deferred_resume"]
        assert resumed is not None
        assert resumed["messages_json"] == messages_json
        assert resumed["seed"]["calls"][0]["tool_call_id"] == TC_ID
        assert resumed["answer"] == ANSWER
        # Session transcript: the user answer recorded honestly (never fabricated
        # into the model transcript — it enters via DeferredToolResults).
        session_id = store.read_state(_T1)["agent_session"]
        transcript = _transcript_records(cfg, session_id)
        assert any(
            record["kind"] == "user" and record["data"]["text"] == ANSWER
            for record in transcript
        )
        # gate_answered row: answer key + seed metadata keys (OQ5).
        answered = [e for e in _events_of(store, _T1) if e["kind"] == "gate_answered"]
        assert answered[-1]["data"] == {
            "answer": ANSWER,
            "tool_call_id": TC_ID,
            "seed": _SEED_FILES,
        }
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 5. Restart survival: replay restores the gate, re-arms, reloads the seed.
# ---------------------------------------------------------------------------
async def test_parked_task_survives_restart(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    messages_json = _defer_messages_json()
    loop = await _park_deferred(cfg, bus, store, runs, messages_json=messages_json)
    parked_seed = _read_seed(store, _T1, "deferred_seed.json")
    parked_messages = _read_seed(store, _T1, "deferred_messages.json")
    # Kill the loop (the "restart") — timer + records die with the process.
    await loop.stop()
    assert store.read_state(_T1)["state"] == "WAITING_INPUT"
    # New TaskLoop, replay: gate restored, seed reloaded from disk.
    runs2: list[dict] = []
    loop2 = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_recording_factory(runs2, _deferred_planner(parked_messages,
                                                                    "resumed after restart")),
        router=_Router(_analysis("seed", mode="gate")),
    )
    counts = loop2.replay()
    assert counts["gate_restored"] == 1
    assert store.read_state(_T1)["state"] == "WAITING_INPUT"
    record = loop2._gates[_T1]
    assert record["deferred"] is not None
    assert record["deferred"]["seed"] == parked_seed
    assert record["deferred"]["messages_json"] == parked_messages
    assert record["restored"] is True
    loop2.start()  # re-arms restored timers (F15 fresh full window)
    try:
        await loop2.answer(_T1, ANSWER)
        await _wait_state(store, _T1, "DONE", timeout=8.0)
        assert "resumed after restart" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        # The ORIGINAL in-run history drove the resume (all_messages_json
        # round-trip through the parked file, not a rebuilt approximation).
        assert len(runs2) == 1
        assert runs2[0]["deferred_resume"]["messages_json"] == parked_messages
        assert runs2[0]["deferred_resume"]["seed"]["calls"][0]["tool_call_id"] == TC_ID
        # gates.jsonl: opened .. restored (re-arm) .. answered.
        rows = [r["event"] for r in _gate_rows(cfg) if r["task_id"] == _T1]
        assert "restored" in rows and rows[-1] == "answered"
    finally:
        await loop2.stop()


# ---------------------------------------------------------------------------
# 6. Subprocess adapters keep the EXACT prompt-append resume (no deferred).
# ---------------------------------------------------------------------------
async def test_subprocess_adapters_keep_prompt_append_resume(
    tmp_volume: Any, cfg: Any
) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    question = "which env?"

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        del spec
        if index == 0:
            return [(0.0, StreamEvent(kind="system", raw=json.dumps({"gate_question": question})))]
        return [(0.0, _final("final after answer"))]

    store.create_inbox_task("subprocess park marker", origin="user", task_id=_T1)
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_recording_factory(runs, planner),
        router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        # No deferred machinery on a subprocess park.
        assert not (store.root / _T1 / "deferred_messages.json").exists()
        assert not (store.root / _T1 / "deferred_seed.json").exists()
        await loop.answer(_T1, ANSWER)
        await _wait_state(store, _T1, "DONE")
        assert len(runs) == 2
        resumed = runs[1]
        # The resume prompt is the EXACT prompt-append (byte-identical flow).
        assert resumed["prompt"] == f"{runs[0]['prompt']}\n\n## User answer\n{ANSWER}"
        assert "which env?" not in resumed["prompt"]
        assert resumed["deferred_resume"] is None
        # gate_answered keeps the legacy single-key payload (OQ5: unchanged).
        answered = [e for e in _events_of(store, _T1) if e["kind"] == "gate_answered"]
        assert answered[-1]["data"] == {"answer": ANSWER}
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 7. POST /api/tasks/answer/{task_id} drives the SAME deferred resume path.
# ---------------------------------------------------------------------------
def test_gateway_answer_endpoint_unchanged(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _Bus()
    manager = SessionManager(cfg, bus=bus)
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    messages_json = _defer_messages_json()
    store.create_inbox_task("gateway deferred marker", origin="user", task_id=_T1)
    loop = TaskLoop(
        cfg, manager, bus=bus,
        adapter_factory=_recording_factory(runs, _deferred_planner(messages_json,
                                                                  "final via gateway answer")),
        router=_Router(_analysis("seed", mode="gate")),
    )
    app = create_app(
        cfg, bus, manager, router_brain=_fake_brain(cfg), adapters={}, taskloop_=loop
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        # Start/stop the TaskLoop on the SAME event loop the app runs on (the
        # TestClient portal loop) — the r10 gap-1 wiring pattern verbatim.
        loop.start()
        try:
            yield
        finally:
            await loop.stop()

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            response = client.get(f"/api/task/{_T1}", headers=_HEADERS())
            if response.status_code == 200 and response.json().get("state") == "WAITING_INPUT":
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"task {_T1} never reached WAITING_INPUT via the route")
        answer_response = client.post(
            f"/api/tasks/answer/{_T1}", json={"text": ANSWER}, headers=_HEADERS()
        )
        assert answer_response.status_code == 200
        assert answer_response.json() == {"ok": True, "task_id": _T1, "state": "DONE"}
        assert "final via gateway answer" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        assert len(runs) == 2
        assert runs[1]["deferred_resume"] is not None
        assert runs[1]["deferred_resume"]["messages_json"] == messages_json
        answered = [e for e in _events_of(store, _T1) if e["kind"] == "gate_answered"]
        assert answered[-1]["data"]["seed"] == _SEED_FILES


def _HEADERS() -> dict:
    return {"X-API-Key": "test-key-123"}  # conftest tmp_volume BOB_HARNESS_API_KEY


# ---------------------------------------------------------------------------
# 8. gate.answered bus topic (Discord parity) drives the same answer().
# ---------------------------------------------------------------------------
async def test_discord_gate_answered_consumed(tmp_volume: Any, cfg: Any) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = _RealBus()  # the REAL bus: the taskloop sink consumes gate.answered
    store = TaskStore(cfg, bus=bus)
    runs: list[dict] = []
    messages_json = _defer_messages_json()
    loop = await _park_deferred(cfg, bus, store, runs,
                                final_text="resumed via discord answer",
                                messages_json=messages_json)
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        # The Discord payload shape (task_id as T<id>).
        await bus.publish(
            "gate.answered",
            {"task_id": f"T{_T1}", "answer": ANSWER, "channel": "discord"},
        )
        await _wait_state(store, _T1, "DONE")
        assert "resumed via discord answer" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        assert len(runs) == 2
        assert runs[1]["deferred_resume"]["messages_json"] == messages_json
        answered = [e for e in _events_of(store, _T1) if e["kind"] == "gate_answered"]
        assert answered[-1]["data"] == {
            "answer": ANSWER,
            "tool_call_id": TC_ID,
            "seed": _SEED_FILES,
        }
    finally:
        await loop.stop()
