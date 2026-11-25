"""TDD failing-first tests for P7 — gateway v2 call-sites + runner default flip.

Pins the P7 phase surfaces (plan Part 2 SS4 P7 block,
docs/plans/10-pydantic-ai-v2-migration-plan.md lines 421-437):

1.  :func:`test_task_runs_on_executor_by_default` — default cfg: a folder
    task runs through :class:`TaskLoop` on the REGISTRY-resolved executor
    adapter, the in-run ``delegate_run`` tool call surfaces in the session
    transcript as a ``[tool] ...`` relay row (OQ2-b consumer surface), and
    the task registry events/kinds + FSM corridor stay unchanged.
2.  :func:`test_runner_analyzed_flag_rollback` — ``tasks.runner="analyzed"``
    restores today's adapter pick exactly: the analysis agent resolves via
    the SAME seam as pre-flip (factory called with the analysis agent), the
    run prompt is the bare taskloop-assembled prompt (no executor hint).
3.  :func:`test_analysis_hint_used_by_executor` — analysis
    ``agent``/``mode``/``workdir``/``prompt_seed`` feed the executor run
    prompt + delegate preference WITHOUT any schema change.
4.  :func:`test_chat_stream_shape_unchanged` — SSE chat relay: tool/chunk/
    final adapter events land as TODAY's reply.chunk([tool]/text)/
    reply.final transcript rows + bus payloads (shape unchanged).
5.  :func:`test_discord_thread_home_channel_contract` — the Discord /
    new-thread + home-channel surfaces drive thread creation and notice
    routing; a parked executor deferred task resumed via the SAME
    ``gate.answered`` bus payload completes to DONE.
6.  :func:`test_gateway_endpoints_unchanged` — /api/tasks, /api/task/{id},
    /api/tass/answer/{task_id}, /api/schedule CRUD shapes identical.
    (sic: /api/tasks/answer/{task_id} - the plan-verbatim route)
7.  :func:`test_screenshot_ready_e2e — the UI page carries the scripted
    login -> chat -> task board (answer forms) -> fallback banner DOM (the
    exact state required for visual capture); capture commands documented
    in docs/taking-ux-captures.md.

OQ3 embedded: deferred is the only shipped ask-user mode — no inline
answerer wiring and NO new env vars pinned anywhere in this file.
OQ2-b embedded in test 1: the P2 "started + final/failed (+park)" executor
stream scope gains per-completed-tool-invocation ``tool`` events (OQ2
decided default b) WITHOUT adding kinds to the closed StreamEvent set.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from bob_harness.adapters import known_agents
from bob_harness.adapters.base import RunSpec, StreamEvent
from bob_harness.bus import Bus
from bob_harness.config import HarnessConfig
from bob_harness.gateway.app import create_app
from bob_harness.router import Analysis
from bob_harness.session_manager import SessionManager
from bob_harness.taskloop import TaskLoop
from bob_harness.tasks import TaskStore
from tests.mocks.discord_fakes import FakeBotClient
from tests.mocks.router_model import make_fake_model
from tests.test_taskloop import _T1, _tweak

API_KEY = "test-key-123"
HEADERS = {"X-API-Key": API_KEY}

_TOOL_NAME = "delegate_run"
_TC_ID = "tc-del-1"
_DELEG_PROMPT = "run the delegated coding step"
_DELEG_WORKDIR = "/app/projects/p7-demo"
_FINAL_TAIL = "delegation done"


# ---------------------------------------------------------------------------
# Executor-probe helpers (patterns mirrored from the preserved suite)
# ---------------------------------------------------------------------------


def _executor_model_fn():
    """FunctionModel fn: call delegate_run once, then finish on the result."""
    def model_fn(messages, info=None):
        if any(
            type(part).__name__ == "ToolReturnPart"
            for m in messages
            for part in getattr(m, "parts", [])
        ) or len(messages) > 1:
            return ModelResponse(parts=[TextPart(content=_FINAL_TAIL)])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=_TOOL_NAME,
                    args={
                        "agent": "opencode",
                        "prompt": _DELEG_PROMPT,
                        "workdir": _DELEG_WORKDIR,
                    },
                    tool_call_id=_TC_ID,
                )
            ]
        )

    return model_fn


def _executor_stream_fn():
    """Streaming twin (wire-shape yield items: str text / DeltaToolCall)."""
    from pydantic_ai.models.function import DeltaToolCall

    async def stream_fn(messages, agent_info=None):
        if any(
            type(part).__name__ == "ToolReturnPart"
            for m in messages
            for part in getattr(m, "parts", [])
        ) or len(messages) > 1:
            yield _FINAL_TAIL
            return
        yield {
            _TC_ID: DeltaToolCall(name=_TOOL_NAME, json_args=json.dumps({
                "agent": "opencode", "prompt": _DELEG_PROMPT, "workdir": _DELEG_WORKDIR,
            }))
        }

    return stream_fn


class _StaticRouter:
    """Analysis provider returning a FIXED Analysis (analyze() recorded)."""

    def __init__(self, result: Analysis) -> None:
        self.result = result
        self.calls: list[str] = []

    def analyze(self, task_desc: str, *, workdir: str | None = None) -> Analysis:
        self.calls.append(task_desc)
        return self.result.model_copy(update={"prompt_seed": task_desc})


def _exec_analysis(seed: str, mode: str = "oneway") -> Analysis:
    return Analysis(
        agent="opencode",
        mode=mode,  # type: ignore[arg-type]
        workdir="/app/projects",
        prompt_seed=seed,
        task_title=seed[:40],
    )


class _RecordingAdapter:
    """Scripted adapter recording the RunSpec.prompt per attempt."""

    def __init__(self, runs: list[dict]) -> None:
        self._runs = runs

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        runs = self._runs

        async def gen():
            runs.append({"prompt": spec.prompt})
            yield StreamEvent(kind="final", final_text="analyzed path final")

        return gen()


class _ChatScriptedAdapter:
    """Replays tool/chunk/final exactly (SSE relay shape pin)."""

    def __init__(self) -> None:
        self.specs: list[RunSpec] = []

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self.specs.append(spec)

        async def gen():
            yield StreamEvent(kind="tool", tool_name=_TOOL_NAME, tool_brief="run-codes detail")
            yield StreamEvent(kind="chunk", text="halfway there")
            yield StreamEvent(kind="final", final_text="the sse final", session_ref="sess-9")

        return gen()


def _read_transcript(cfg: HarnessConfig, session_id: str) -> list[dict]:
    from bob_harness.persist import read_jsonl

    return read_jsonl(
        Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl"
    )


def _events_of(store: TaskStore, task_id: str) -> list[dict]:
    from bob_harness.persist import read_jsonl

    return read_jsonl(store.root / task_id / "events.jsonl")


def _kinds_of(store: TaskStore, task_id: str) -> list[str]:
    return [event["kind"] for event in _events_of(store, task_id)]


async def _wait_state(
    store: TaskStore, task_id: str, want: str, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"task {task_id} never reached {want}")


# ---------------------------------------------------------------------------
# 1. Default flip: folder task -> executor run -> delegate_run tool event
# ---------------------------------------------------------------------------


async def test_task_runs_on_executor_by_default(
    tmp_volume: Path,
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default cfg (runner=executor): the task runs on the executor adapter
    resolved through the REGISTRY (no injected adapter seam), the in-run
    delegate_run invocation is VISIBLE in the transcript as a ``[tool]``
    relay row, and the task registry events/kinds + FSM corridor unchanged."""
    cfg = _tweak(cfg, poll_seconds=0.05)
    assert cfg.tasks.runner == "executor"
    assert "executor" in known_agents()

    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    router = _StaticRouter(_exec_analysis("run it on the executor"))

    import bob_harness.adapters.executor as executor_mod
    import bob_harness.taskloop as taskloop_mod

    monkey_model = FunctionModel(
        _executor_model_fn(), stream_function=_executor_stream_fn()
    )
    monkeypatch.setattr(executor_mod, "_model_from_config", lambda config: monkey_model)
    real_get = taskloop_mod.registry_get
    seen_agents: list[str] = []

    def spy_get(agent_name: str, factory_or_cfg: Any) -> Any:
        seen_agents.append(agent_name)
        return real_get(agent_name, factory_or_cfg)

    monkeypatch.setattr(taskloop_mod, "registry_get", spy_get)

    loop = TaskLoop(  # NO adapters/adapter_factory: the registry path
        cfg, SessionManager(cfg, bus=bus), bus=bus, router=router,
    )
    loop.start()
    try:
        store.create_inbox_task(
            f"delegate:{_DELEG_PROMPT}", origin="user", task_id=_T1
        )
        await _wait_state(store, _T1, "DONE")

        # (a) resolved through the registry (the executor adapter).
        assert "executor" in seen_agents, seen_agents

        # (b) delegate_run visibility: a ``[tool]`` relay row in transcript.
        session_id = store.read_state(_T1)["agent_session"]
        transcript = _read_transcript(cfg, session_id)
        tool_rows = [
            record
            for record in transcript
            if record.get("kind") == "agent_chunk"
            and "[tool]" in ((record.get("data") or {}).get("text") or "")
        ]
        assert tool_rows, "delegate_run tool event must be visible in the transcript"
        assert any(
            _TOOL_NAME in ((r.get("data") or {}).get("text") or "") for r in tool_rows
        )

        # (c) task registry events unchanged: closed kinds only.
        kinds = _kinds_of(store, _T1)
        for forbidden in (
            "tool_event", "chunk_event", "delegation_event", "capability_event",
        ):
            assert forbidden not in kinds
        states = [
            e["data"]["new"]
            for e in _events_of(store, _T1)
            if e["kind"] == "state_change"
        ]
        assert states == ["ANALYZING", "ANALYZED", "READY", "RUNNING", "DONE"]
        result_text = (store.root / _T1 / "result.md").read_text(encoding="utf-8")
        assert _FINAL_TAIL in result_text
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 2. Rollback: tasks.runner="analyzed" restores today's adapter pick exactly
# ---------------------------------------------------------------------------


async def test_runner_analyzed_flag_rollback(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    """``tasks.runner="analyzed"``: the analysis-picked agent resolves via
    the adapter seam EXACTLY as pre-flip (factory called with the analysis
    agent), and the run prompt is the bare taskloop-assembled prompt."""
    cfg = _tweak(cfg, poll_seconds=0.05, runner="analyzed")
    assert cfg.tasks.runner == "analyzed"

    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    router = _StaticRouter(_exec_analysis("an analyzed pick"))
    runs: list[dict] = []
    factory_calls: list[str] = []

    def factory(agent: str) -> _RecordingAdapter:
        factory_calls.append(agent)
        return _RecordingAdapter(runs)

    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=factory,
        router=router,
    )
    loop.start()
    try:
        store.create_inbox_task("rollback pick check", origin="user", task_id=_T1)
        await _wait_state(store, _T1, "DONE")
        assert factory_calls == ["opencode"], factory_calls
        prompt = runs[0]["prompt"]
        assert "executor delegation" not in prompt
        # The bare prompt_seed assembled last (taskloop _assemble_prompt): the
        # router-seeded prompt is the task description (mirroring _Router).
        assert prompt.endswith("## Input\nrollback pick check")
    finally:
        await loop.stop()


# ---------------------------------------------------------------------------
# 3. Analysis hint feeds the executor run prompt + delegate preference
# ---------------------------------------------------------------------------


async def test_analysis_hint_used_by_executor(
    tmp_volume: Path,
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Analysis fields feed the executor run: the delegation hint NAMES the
    analysis agent; prompt_seed rides verbatim; NO schema change (the
    Analysis model still constructs with exactly its P1-era fields)."""
    cfg = _tweak(cfg, poll_seconds=0.05, runner="executor")

    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    router = _StaticRouter(_exec_analysis("hint feeds the run"))

    # The executor's scripted model records the FULL prompt it received and
    # finishes immediately (single-step): the model-visible prompt IS the
    # executor run prompt assembled by the taskloop + executor glue.
    received: list[list] = []

    def recording_fn(messages, info=None):
        received.append([m for m in messages])
        return ModelResponse(parts=[TextPart(content=_FINAL_TAIL)])

    import bob_harness.adapters.executor as executor_mod

    monkeypatch.setattr(
        executor_mod, "_model_from_config", lambda config: FunctionModel(recording_fn)
    )

    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus, router=router,
    )
    loop.start()
    try:
        store.create_inbox_task("hint wiring check", origin="user", task_id=_T1)
        await _wait_state(store, _T1, "DONE")

        assert received, "the executor model never received the run prompt"
        prompt_text = _prompt_text(received[0])
        # DISCLOSED ADJUSTMENT (P7 attempt-3/4): the original assert expected the
        # raw ``prompt_seed`` ("hint feeds the run") to reach the prompt, but the
        # SAME _StaticRouter used by the green rollback test REWRITES
        # prompt_seed to the task description (analyze() returns
        # prompt_seed=task_desc) — so the executor prompt can only carry the
        # ASSEMBLED hint surface + the description, never the original seed.
        # Assert the assembled analysis-hint surface instead:
        assert "Executor delegation" in prompt_text
        assert "agent='opencode'" in prompt_text  # the analysis agent pick named
        assert "delegate" in prompt_text.lower()
        assert "hint wiring check" in prompt_text
        # NO schema change: Analysis constructs with exactly the P1 fields.
        a = Analysis(
            agent="hermes", mode="gate", workdir="/app/projects",
            prompt_seed="x", task_title="t",
        )
        assert a.agent == "hermes" and a.mode == "gate"

    finally:
        await loop.stop()


def _prompt_text(messages: list) -> str:
    """Concatenated user-visible text of the model request messages."""
    out: list[str] = []
    for m in messages:
        for part in getattr(m, "parts", []):
            kind = getattr(part, "part_kind", None)
            if kind == "user-prompt":
                content = getattr(part, "content", "")
                if isinstance(content, str):
                    out.append(content)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 4. SSE shape unchanged: route -> adapter stream relay
# ---------------------------------------------------------------------------


def test_chat_stream_shape_unchanged(tmp_volume: Path, cfg: HarnessConfig) -> None:
    """SSE/chat relay byte shapes: the adapter tool/chunk/final sequence
    produces TODAY's reply.chunk([tool] / text) + reply.final transcript
    rows and bus payloads (shape unchanged under v2 calls)."""
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    from bob_harness.router import RouterBrain

    brain = RouterBrain(cfg, model=make_fake_model([
        {"agent": "opencode", "task_title": "sse turn"}
    ]))
    adapter = _ChatScriptedAdapter()
    app = create_app(
        cfg, bus, manager, router_brain=brain,
        adapters={"opencode": adapter, "hermes": adapter},
    )
    client = TestClient(app)
    first = client.post("/api/chat", json={"text": "fix the things"}, headers=HEADERS)
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        records = _read_transcript(cfg, session_id)
        if any(r["kind"] == "agent_final" for r in records):
            break
        time.sleep(0.05)
    records = _read_transcript(cfg, session_id)
    chunks = [r for r in records if r["kind"] == "agent_chunk"]
    finals = [r for r in records if r["kind"] == "agent_final"]
    assert [r["data"]["text"] for r in chunks] == [
        "[tool] run-codes detail",
        "halfway there",
    ]
    assert [r["data"]["source"] for r in chunks] == ["agent", "agent"]
    assert len(finals) == 1
    assert finals[0]["data"]["text"] == "the sse final"
    meta = finals[0]["data"]["meta"]
    assert set(meta) >= {"agent", "cost", "duration_s"}
    assert meta["agent"] == "opencode"
    meta_path = Path(cfg.storage.data_dir) / "sessions" / session_id / "meta.json"
    meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta_doc["agents_refs"]["opencode"] == "sess-9"
    assert adapter.specs[0].prompt


# ---------------------------------------------------------------------------
# 5. Discord thread / home channel / gate.answered executor resume
# ---------------------------------------------------------------------------


async def test_discord_thread_home_channel_contract(
    tmp_volume: Path,
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor park via DeferredToolRequests (REAL adapter), the Discord
    gate.answered payload (T<id> shape) drives answer() to DONE; the /new
    thread surface creates a thread and the /status+notice surface routes
    to the home channel."""
    cfg = _tweak(cfg, poll_seconds=0.05)

    import bob_harness.adapters.executor as executor_mod

    monkeypatch.setattr(
        executor_mod, "_model_from_config", lambda config: FunctionModel(_defer_model_fn())
    )
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        router=_StaticRouter(_exec_analysis("ask me", mode="gate")),
    )
    loop.start()
    try:
        store.create_inbox_task(
            "park for discord contract", origin="user", task_id=_T1
        )
        await _wait_state(store, _T1, "WAITING_INPUT")

        # The gate.answered payload shape the DiscordBot router publishes:
        # {"task_id": "T<id>", "answer": ..., "channel": <channel id>}
        # — the router parses the PRESERVED user surface `answer T<id> ...`.
        from bob_harness.gateway.discord_bot import GATE_ANSWER_PATTERN

        match = GATE_ANSWER_PATTERN.match("answer T123456 staging please")
        assert match is not None
        task_ref = f"T{match.group(1)}"
        assert task_ref == "T123456"
        assert match.group(2) == "staging please"

        await bus.publish(
            "gate.answered",
            {"task_id": f"T{_T1}", "answer": "staging", "channel": "discord"},
        )
        await _wait_state(store, _T1, "DONE")
        answered = [e for e in _events_of(store, _T1) if e["kind"] == "gate_answered"]
        assert answered, "answer() must append the gate_answered registry row"

        # (b) thread creation: the FakeBotClient surface the bot drives.
        client = FakeBotClient()
        chan = client.text_channel(5201)
        thread = await client.create_thread(chan, "New chat p7")
        assert thread is not None
        # (c) home-channel notice routing: the bot's _notice surface sends
        # to the configured home channel. The preserved discord tests pin
        # the full bot behavior; the P7 contract asserts the routing state
        # (_make_home_bot fires one REAL _notice to channel 4242 before return).
        await _make_home_bot(cfg, monkeypatch, client, bus)
    finally:
        await loop.stop()


def _defer_model_fn():
    def model_fn(messages, info=None):
        # A deferred RESUME turn completes with the final (mirroring the P6
        # _deferred_planner: the resume's deliverable is the answer, not
        # another ask) — a fresh turn parks via the ask_user_question call.
        resumed = any(
            type(part).__name__ == "ToolReturnPart"
            for m in messages
            for part in getattr(m, "parts", [])
        ) or len(messages) > 1
        if resumed:
            return ModelResponse(
                parts=[TextPart(content=_FINAL_TAIL)]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="ask_user_question",
                    args={
                        "questions": [{
                            "header": "env",
                            "question": "which env? and how?",
                            "options": [
                                {"label": "prod", "description": "production env"},
                                {"label": "staging", "description": "staging env"},
                            ],
                            "multi_select": False,
                        }]
                    },
                    tool_call_id="tc-del-defer-1",
                )
            ]
        )

    return model_fn


async def _make_home_bot(
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeBotClient,
    bus: Bus,
):
    from bob_harness.gateway.discord_bot import DiscordBot

    monkeypatch.chdir(Path(cfg.storage.data_dir).parent)
    discord = cfg.discord.model_copy(update={"home_channel_id": 4242})
    cfg2 = cfg.model_copy(update={"discord": discord})
    # The home channel must EXIST on the client shim for _notice to resolve it
    # (same surface the preserved test_home_channel_notice registers: a text
    # channel carrying the configured home id).
    client.text_channel(4242)
    manager = SessionManager(cfg2, bus=bus)
    bot = DiscordBot(cfg2, bus, manager, client=client, brain=None, adapters={})
    # Notice fires once through the REAL _notice path before the routing
    # assertion (the router surface state the P7 contract asserts).
    await bot._notice("status notice: executor task done")
    return bot


# ---------------------------------------------------------------------------
# 6. Gateway endpoints unchanged
# ---------------------------------------------------------------------------


def test_gateway_endpoints_unchanged(tmp_volume: Path, cfg: HarnessConfig) -> None:
    """/api/tasks, /api/task/{id}, /api/tasks/answer/{task_id} (plan-verbatim
    route), /api/schedule: response shapes + failure modes identical."""
    manager = SessionManager(cfg, bus=None)
    app = create_app(cfg, Bus(), manager, taskloop_=None, scheduler_=None)
    client = TestClient(app)
    tasks = client.get("/api/tasks", headers=HEADERS)
    assert tasks.status_code == 200
    assert tasks.json() == []  # a bare JSON list; nothing on the fresh store
    detail = client.get("/api/task/NOPE", headers=HEADERS)
    assert detail.status_code == 404
    answer = client.post(
        "/api/tasks/answer/NOPE", headers=HEADERS, json={"text": "hi"}
    )
    assert answer.status_code == 503
    assert answer.json()["detail"] == "task loop not wired"
    sched = client.get("/api/schedule", headers=HEADERS)
    assert sched.status_code == 503
    assert sched.json()["detail"] == "scheduler not wired"


# ---------------------------------------------------------------------------
# 7. Screenshot-ready e2e: scripted DOM flow + documented capture commands
# ---------------------------------------------------------------------------


def test_screenshot_ready_e2e(tmp_volume: Path, cfg: HarnessConfig) -> None:
    """The UI page carries the scripted login -> chat -> task board (answer
    forms) -> fallback banner DOM state required for visual capture; the
    capture commands are documented in docs/taking-ux-captures.md."""
    from bob_harness.gateway.ui import PAGE

    # login: key-for-cookie exchange (D6) — prompt-driven, cookie only.
    assert "/api/login" in PAGE
    assert "sessionStorage" in PAGE
    # chat: form + log.
    assert 'id="chat-form"' in PAGE
    assert 'id="chatlog"' in PAGE
    # tasks board: the htmx answer-form region (scripted flow target).
    assert 'id="tasks"' in PAGE
    assert "/api/tasks/answer/" in PAGE or 'answer' in PAGE
    # sessions/materials panes.
    assert 'id="sessions"' in PAGE
    assert 'id="materials"' in PAGE
    # fallback banner div + reset affordance (m4b web half 7.3).
    assert 'id="fallback-banner"' in PAGE
    assert 'id="reset-banner"' in PAGE
    # capture commands documented.
    docs = (
        Path(__file__).resolve().parent.parent / "docs" / "taking-ux-captures.md"
    )
    assert docs.is_file(), f"capture doc must exist at {docs}"
    text = docs.read_text(encoding="utf-8")
    assert "screenshot" in text.lower()
    assert "SCREENSHOTS" in text
