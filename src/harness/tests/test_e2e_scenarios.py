"""R12 end-to-end battery, scenarios 1-7 (zero network).

Every test drives the REAL harness components (FastAPI gateway / DiscordBot,
SessionManager, persist files under ``cfg.storage.data_dir``) over the two
documented test seams only:

- the router seam is the R5 fake model OBJECT (``tests.mocks.router_model``);
- the adapter seam is a local :class:`ScriptedAdapter` (run(spec) -> async
  iterator of :class:`StreamEvent`), never a real provider or subprocess.

Scenarios 5-7 live at the bottom of this module.

Boundary notes (documented because the seam is genuinely internal):

- Scenario 1 drives ``DiscordBot.on_message`` (the real inbound hook the
  discord.py event loop calls) with the six-method ``FakeBotClient``. It does
  NOT go through TestClient: Discord inbound is a separate real entrypoint that
  mirrors the gateway chat pipeline line-for-line (see discord_bot module docs).
- Scenario 3: no production caller of ``SkillsEngine.capture`` exists in the
  tree yet (R9/R12 wiring), so the test starts the capture from the real chat
  transcript's ``SKILL_DRAFT`` output and calls the real ``capture`` API with a
  deterministic stub injected at its ``distill`` parameter seam.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import apscheduler.executors.base as _sc6_ex_ns
import apscheduler.schedulers.base as _sc6_base_ns
import apscheduler.triggers.cron as _sc6_cron_ns
import apscheduler.triggers.date as _sc6_date_ns
import apscheduler.triggers.interval as _sc6_interval_ns
import yaml
from fastapi.testclient import TestClient

from harness.adapters.base import RunSpec, StreamEvent
from harness.blocks import parse_blocks
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.gateway.app import create_app
from harness.gateway.discord_bot import DiscordBot
from harness.persist import read_jsonl
from harness.router import Analysis, RouterBrain
from harness.scheduler import SchedulerEngine
from harness.session_manager import SessionManager
from harness.skills import SkillsEngine
from harness.taskloop import TaskLoop
from harness.tasks import TaskStore
from tests.mocks.discord_fakes import FakeBotClient, FakeMember, FakeUser, make_message
from tests.mocks.router_model import make_fake_model

API_KEY = "test-key-123"  # conftest tmp_volume feeds HARNESS_API_KEY with this
HEADERS = {"X-API-Key": API_KEY}

# Torn-tail fragment: identical to the F6/F17 replay recipe in test_replay.py.
_TORN_JSON = b'{"ts": "2026-11-12T10:00:00Z", "kind": "agent_chunk"'


class ScriptedAdapter:
    """FakeAdapter-like test adapter: replays a fixed ``StreamEvent`` list,
    awaiting ``delay_s`` before each event so tests control stream timing."""

    def __init__(self, events: list[StreamEvent], delay_s: float = 0.15) -> None:
        self._events = list(events)
        self._delay_s = delay_s
        self.specs: list[RunSpec] = []

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self.specs.append(spec)
        for event in self._events:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield event


def make_app(
    cfg: HarnessConfig,
    *,
    outputs: list | None = None,
    events: list[StreamEvent] | None = None,
    adapters: object = None,
    delay_s: float = 0.15,
) -> tuple:
    """Fresh app per test: fake router model + a scripted adapter behind the
    R-3 seams (never a real provider/model call)."""
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    model_outputs = outputs if outputs is not None else [
        {"agent": "opencode", "task_title": "demo task"}
    ]
    brain = RouterBrain(cfg, model=make_fake_model(model_outputs))
    adapter = ScriptedAdapter(
        events
        if events is not None
        else [
            StreamEvent(kind="chunk", text="a"),
            StreamEvent(kind="chunk", text="b"),
            StreamEvent(kind="final", final_text="done"),
        ],
        delay_s=delay_s,
    )
    if adapters is None:
        adapters = {"opencode": adapter, "hermes": adapter}
    app = create_app(cfg, bus, manager, router_brain=brain, adapters=adapters)
    return app, adapter, bus, manager


def parse_sse(lines: list[str]) -> list[dict]:
    """Parse SSE wire lines into event dicts (field order irrelevant)."""
    events: list[dict] = []
    current: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            if current:
                events.append(current)
                current = {}
            continue
        key, _, value = line.partition(": ")
        current[key] = value
    if current:
        events.append(current)
    return events


def read_until_final(stream_response) -> list[str]:
    """SSE lines through (and including) the ``reply.final`` data line."""
    lines: list[str] = []
    it = stream_response.iter_lines()
    seen_final_event = False
    for line in it:
        lines.append(line)
        if "event: reply.final" in line:
            seen_final_event = True
        elif seen_final_event and line.startswith("data: "):
            break
    return lines


def transcript_records(cfg: HarnessConfig, session_id: str) -> list[dict]:
    return read_jsonl(Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl")


def wait_idle(client: TestClient, session_id: str, timeout_s: float = 6.0) -> str:
    deadline = time.monotonic() + timeout_s
    state = ""
    while time.monotonic() < deadline:
        state = client.get(f"/api/session/{session_id}", headers=HEADERS).json().get("state")
        if state == "idle":
            break
        time.sleep(0.05)
    return state


def wait_state(client: TestClient, session_id: str, wanted: str, timeout_s: float = 6.0) -> str:
    deadline = time.monotonic() + timeout_s
    state = ""
    while time.monotonic() < deadline:
        state = client.get(f"/api/session/{session_id}", headers=HEADERS).json().get("state")
        if state == wanted:
            break
        time.sleep(0.01)
    return state


# ----------------------------------------------------------------------
# Scenario 1: Discord message -> real DiscordBot turn -> router -> adapter
# ----------------------------------------------------------------------
async def test_chat_path_discord_gateway_router_adapter_final(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    """Drive the real Discord inbound hook (no bot token / gateway connection):
    one guild message through ``DiscordBot.on_message`` -> allowlist -> session
    routing via the fake router model -> scripted hermes adapter stream ->
    route line / chunks / final on the bus -> Discord fake receives the reply,
    transcript carries the same events and the session settles idle."""
    cfg = cfg.model_copy(
        update={"discord": cfg.discord.model_copy(update={"chunk_ms": 1.0})}
    )
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    brain = RouterBrain(
        cfg, model=make_fake_model([{"agent": "hermes", "task_title": "discord triage"}])
    )
    hermes = ScriptedAdapter(
        [
            StreamEvent(kind="chunk", text="h1"),
            StreamEvent(kind="chunk", text="h2"),
            StreamEvent(kind="final", final_text="hermes done"),
        ],
        delay_s=0.05,
    )
    opencode = ScriptedAdapter([StreamEvent(kind="final", final_text="unused")], delay_s=0.0)
    client = FakeBotClient()
    channel = client.text_channel(5100)
    bot = DiscordBot(
        cfg,
        bus,
        manager,
        client=client,
        brain=brain,
        adapters={"hermes": hermes, "opencode": opencode},
    )
    await bot.start()
    try:
        message = make_message(
            client,
            "research the release notes",
            channel=channel,
            user=FakeUser(4321, "alice"),
            member=FakeMember(4321, roles=[]),
        )
        await bot.on_message(message)
        await bot.wait_turns()
        for _ in range(4):  # let the outbound pump + coalesced edits settle
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)

        session = await manager.get_or_create(channel_handle=f"discord:{channel.id}")
        assert session.state == "idle"
        assert len(hermes.specs) == 1 and opencode.specs == []
        assert "research the release notes" in hermes.specs[0].prompt

        records = transcript_records(cfg, session.id)
        route_rows = [row for row in records if row.get("kind") == "router"]
        assert route_rows and route_rows[-1]["data"]["agent"] == "hermes"
        chunks = [row for row in records if row.get("kind") == "agent_chunk"]
        assert [row["data"]["text"] for row in chunks] == ["h1", "h2"]
        finals = [row for row in records if row.get("kind") == "agent_final"]
        assert finals and finals[-1]["data"]["text"] == "hermes done"

        # The fake Discord channel observed the route line AND the final reply.
        sends = client.sent_to[channel.id]
        assert any("[routed to hermes] discord triage" in text for text in sends)
        assert client.edits and client.edits[-1][1] == "hermes done"
    finally:
        await bot.stop()


# ----------------------------------------------------------------------
# Scenario 2: handoff chain (parent hermes -> child re-routed to opencode)
# ----------------------------------------------------------------------
def test_handoff_chain_hermes_to_simple_drop_spawn_result(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    """Parent chat routes to hermes; its HANDOFF fence is dropped through the
    real ``POST /api/handoff`` -> ``SessionManager.create_child`` spawn. Pinned
    real behavior: the fence's ``AGENT`` field is validated but NOT used for the
    child route — ``/api/handoff`` re-runs ``RouterBrain.route_async`` on the
    prompt, so the router mock's second output (opencode) decides. The result
    chain is observable as two distinct adapter runs and the per-session
    transcripts (parent final -> child ``session_link`` -> child final)."""
    hermes = ScriptedAdapter(
        [
            StreamEvent(kind="chunk", text="studying"),
            StreamEvent(kind="final", final_text="parent study complete"),
        ],
        delay_s=0.02,
    )
    opencode = ScriptedAdapter(
        [
            StreamEvent(kind="chunk", text="implementing"),
            StreamEvent(kind="final", final_text="child implementation complete"),
        ],
        delay_s=0.02,
    )
    app, _adapter, _bus, _manager = make_app(
        cfg,
        outputs=[
            {"agent": "hermes", "task_title": "study the deploy plan"},
            {"agent": "opencode", "task_title": "implement the follow-up"},
        ],
        adapters={"hermes": hermes, "opencode": opencode},
    )
    with TestClient(app) as client:
        parent_resp = client.post(
            "/api/chat", json={"text": "research the deploy plan"}, headers=HEADERS
        )
        assert parent_resp.status_code == 200
        assert parent_resp.json()["route"]["agent"] == "hermes"
        parent_id = parent_resp.json()["session_id"]
        assert wait_idle(client, parent_id) == "idle"

        # AGENT deliberately differs from the router's child decision.
        handoff_text = (
            "```HANDOFF\nAGENT: hermes\nPROMPT: implement the follow-up now\n```"
        )
        child_resp = client.post(
            "/api/handoff",
            json={"session_id": parent_id, "text": handoff_text},
            headers=HEADERS,
        )
        assert child_resp.status_code == 200
        child = child_resp.json()
        assert child["parent_session"] == parent_id
        assert child["route"]["agent"] == "opencode"  # router wins over fence AGENT
        child_id = child["session_id"]
        assert wait_idle(client, child_id) == "idle"

        # Two distinct adapters ran exactly once each, on the right prompts.
        assert len(hermes.specs) == 1 and len(opencode.specs) == 1
        assert "research the deploy plan" in hermes.specs[0].prompt
        assert "implement the follow-up now" in opencode.specs[0].prompt

        parent_rows = transcript_records(cfg, parent_id)
        assert any(
            row.get("kind") == "session_link" and row["data"] == {"child": child_id}
            for row in parent_rows
        )
        assert any(
            row.get("kind") == "agent_final"
            and row["data"]["text"] == "parent study complete"
            for row in parent_rows
        )
        child_rows = transcript_records(cfg, child_id)
        assert any(
            row.get("kind") == "session_link" and row["data"] == {"parent": parent_id}
            for row in child_rows
        )
        assert any(
            row.get("kind") == "router" and row["data"]["agent"] == "opencode"
            for row in child_rows
        )
        assert any(
            row.get("kind") == "agent_final"
            and row["data"]["text"] == "child implementation complete"
            for row in child_rows
        )


# ----------------------------------------------------------------------
# Scenario 3: skill capture E2E with a stubbed distill stage
# ----------------------------------------------------------------------
def test_skill_capture_e2e_stub_distill(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    """Chat final carries a SKILL_DRAFT block; the real ``SkillsEngine.capture``
    applies it with a deterministic stub injected at the ``distill`` seam.
    Assert the real artifact (SKILL.md + rebuilt INDEX.md + materials listing)
    and that the stub saw exactly (session_id, verbatim draft body)."""
    draft_doc = (
        "---\n"
        "name: Release Runbook\n"
        "description: Capture the release runbook.\n"
        "---\n"
        "## Steps\n1. TODO tag the build.\n"
    )
    final_text = "captured the procedure\n\n```SKILL_DRAFT\n" + draft_doc + "```"
    app, _adapter, _bus, _manager = make_app(
        cfg,
        outputs=[{"agent": "hermes", "task_title": "capture the runbook"}],
        events=[
            StreamEvent(kind="chunk", text="working"),
            StreamEvent(kind="final", final_text=final_text),
        ],
        delay_s=0.02,
    )
    calls: list[tuple[str, str]] = []

    def stub_distill(session_id: str, body_md: str) -> str:
        calls.append((session_id, body_md))
        return body_md.replace("TODO", "DISTILLED")

    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"text": "capture this runbook"}, headers=HEADERS)
        assert chat.status_code == 200
        session_id = chat.json()["session_id"]
        assert wait_idle(client, session_id) == "idle"

        records = transcript_records(cfg, session_id)
        final_row = [row for row in records if row.get("kind") == "agent_final"][-1]
        blocks = parse_blocks(final_row["data"]["text"])
        drafts = [block for block in blocks if block.tag == "SKILL_DRAFT" and block.ok]
        assert len(drafts) == 1

        result = SkillsEngine(cfg).capture(session_id, drafts[0], distill=stub_distill)
        assert result["slug"] == "release-runbook"
        assert result["created_new"] is True
        skill_path = tmp_volume / "skills" / "release-runbook" / "SKILL.md"
        assert result["path"] == str(skill_path)
        expected_body = drafts[0].body.replace("TODO", "DISTILLED")
        assert skill_path.read_text(encoding="utf-8") == expected_body
        assert calls == [(session_id, drafts[0].body)]
        index = (tmp_volume / "skills" / "INDEX.md").read_text(encoding="utf-8")
        assert "- release-runbook: Capture the release runbook." in index

        materials = client.get("/materials?format=json", headers=HEADERS).json()
        assert "release-runbook" in materials["skills"]["names"]


# ----------------------------------------------------------------------
# Scenario 4: kill mid-run -> fresh boot replay (F6; F17 is taskloop replay)
# ----------------------------------------------------------------------
def test_restart_replay_e2e_kill_mid_run_boot_replays(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    """A chat run stalls after the agent_running move (adapter never emits a
    final). The transcript tail is torn exactly like test_replay.py's F6
    recipe; a FRESH SessionManager.replay() recovers it: torn tail skipped,
    session marked ``agent_failed`` with the honest interruption note, and a
    second boot is idempotent. A fresh app then serves that recovered state.
    (F17 one-shot task requeue lives in ``TaskLoop.replay``, not this session
    recipe, so it is not re-asserted here.)"""
    app, _adapter, _bus, _manager = make_app(
        cfg,
        outputs=[{"agent": "opencode", "task_title": "stalled run"}],
        events=[StreamEvent(kind="chunk", text="stalled")],
        delay_s=30.0,
    )
    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"text": "start the long build"}, headers=HEADERS)
        assert chat.status_code == 200
        session_id = chat.json()["session_id"]
        assert wait_state(client, session_id, "agent_running") == "agent_running"

        session_dir = Path(cfg.storage.data_dir) / "sessions" / session_id
        transcript_path = session_dir / "transcript.jsonl"
        with transcript_path.open("ab") as handle:  # kill -9 mid-append
            handle.write(_TORN_JSON)

        fresh_bus = Bus()
        fresh_manager = SessionManager(cfg, bus=fresh_bus)
        counts = fresh_manager.replay()
        assert counts == {"sessions": 1, "recovered_failed": 1, "torn_skipped": 1}

        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["state"] == "agent_failed"
        # test_replay.py's torn-tail recipe: the healed fragment survives its
        # own line and the recovery note lands after it as the LAST line.
        raw_lines = transcript_path.read_text(encoding="utf-8").splitlines()
        note = json.loads(raw_lines[-1])
        assert note["kind"] == "note"
        assert note["state"] == "agent_failed"
        assert note["session_id"] == session_id
        assert note["data"] == {
            "reason": "interrupted by restart before completion",
            "from_state": "agent_running",
        }

        # note-then-meta order keeps the second boot idempotent (F6 recipe).
        second = SessionManager(cfg).replay()
        assert second == {"sessions": 1, "recovered_failed": 0, "torn_skipped": 0}

        fresh_app = create_app(
            cfg,
            fresh_bus,
            fresh_manager,
            router_brain=RouterBrain(cfg, model=make_fake_model([])),
            adapters={"opencode": ScriptedAdapter([], delay_s=0.0)},
        )
        with TestClient(fresh_app) as fresh_client:
            payload = fresh_client.get(f"/api/session/{session_id}", headers=HEADERS).json()
            assert payload["session_id"] == session_id
            assert payload["state"] == "agent_failed"


# ----------------------------------------------------------------------
# Scenario 5: composed hand task -> loop -> drop child -> deps gate -> answer
# ----------------------------------------------------------------------
class _Sc5LoopAdapter:
    """Per-attempt scripted adapter (the test_taskloop ``_Adapter`` shape)."""

    def __init__(self, runs: list[dict], planner) -> None:
        self._runs = runs
        self._planner = planner
        self._count = 0

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self._count += 1
        entry: dict = {
            "prompt": spec.prompt,
            "resume_ref": spec.resume_ref,
            "oneway": spec.oneway,
            "start": time.monotonic(),
        }
        index = self._count - 1
        self._runs.append(entry)

        async def gen() -> AsyncIterator[StreamEvent]:
            for delay, event in self._planner(spec, index):
                if delay:
                    await asyncio.sleep(delay)
                entry["end"] = time.monotonic()
                yield event

        return gen()


class _ScMarkerRouter:
    """Local router double: marker substring -> session mode."""

    def __init__(self, modes: dict[str, str] | None = None) -> None:
        self.modes = dict(modes or {})

    def analyze(self, task_desc: str, *, workdir: str | None = None) -> Analysis:
        del workdir
        mode = "oneway"
        for marker, value in self.modes.items():
            if marker in task_desc:
                mode = value
        seed = task_desc if mode != "gate" else f"{task_desc} which env?"
        return Analysis(
            agent="opencode",
            mode=mode,
            workdir="/app/projects",
            prompt_seed=seed,
            task_title=task_desc[:40],
        )


def _sc_events_of(store: TaskStore, task_id: str) -> list[dict]:
    return read_jsonl(store.root / task_id / "events.jsonl")


def _sc_kinds_of(store: TaskStore, task_id: str) -> list[str]:
    return [event["kind"] for event in _sc_events_of(store, task_id)]


async def _sc_wait_state(
    store: TaskStore, task_id: str, want: str, timeout: float = 6.0
) -> dict:
    deadline = time.monotonic() + timeout
    data: dict | None = None
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return data
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached {want}; got {data}")


def _sc_wait_state_sync(
    store: TaskStore, task_id: str, want: str, timeout: float = 6.0
) -> dict:
    deadline = time.monotonic() + timeout
    data: dict | None = None
    while time.monotonic() < deadline:
        data = store.read_state(task_id)
        if data is not None and data.get("state") == want:
            return data
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached {want}; got {data}")


def _sc_states_from(queue, task_id: str) -> list[str]:
    states: list[str] = []
    while True:
        try:
            topic, payload = queue.get_nowait()
        except asyncio.QueueEmpty:
            return states
        if topic == "task.state" and payload.get("task_id") == task_id:
            state = payload.get("new")
            if isinstance(state, str):
                states.append(state)


class _ScCachedTaskStore(TaskStore):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cache: dict[str, object] = {}

    def open_task(self, task_id: str):
        if task_id not in self._cache:
            self._cache[task_id] = super().open_task(task_id)
        return self._cache[task_id]


async def _sc5_force_dep_wait(
    loop: TaskLoop, store: TaskStore, parent_id: str, observed: dict
) -> None:
    parent = None
    while parent is None:
        data = store.read_state(parent_id)
        if data is not None and data.get("state") == "RUNNING":
            parent = loop._store.open_task(parent_id)
        else:
            await asyncio.sleep(0.005)
    async with parent._lock:
        deadline = time.monotonic() + 6.0
        child_id = ""
        while time.monotonic() < deadline:
            inbox = store.list_inbox()
            if inbox:
                child_id = inbox[0]
                observed["child_id"] = child_id
                observed["inbox_count"] = len(inbox)
                observed["task_yaml"] = yaml.safe_load(
                    (store.inbox / child_id / "task.yaml").read_text(encoding="utf-8")
                )
                break
            await asyncio.sleep(0.005)
        if not child_id:
            raise AssertionError("SUBTASK_DROP child never appeared")
        await loop._pick_up(child_id)
        await loop._process(child_id)
        data = await _sc_wait_state(store, child_id, "WAITING_DEPS")
        observed["dep_state"] = data["state"]
        observed["done"] = True


async def test_scenario5_composed_chain_drop_deps_gate_answer_done(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    del tmp_volume
    cfg = cfg.model_copy(
        update={
            "tasks": cfg.tasks.model_copy(
                update={
                    "poll_seconds": 0.05,
                    "gate_timeout_s": 0.4,
                    "max_depth": 2,
                    "max_concurrent": 1,
                }
            )
        }
    )
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    store = _ScCachedTaskStore(cfg, bus=bus)
    parent_id = "AAA23456"
    store.create_inbox_task("hand chore marker", origin="user", task_id=parent_id)
    parent_final = (
        "Groundwork done.\n\n"
        "```SUBTASK_DROP\n"
        "description: child chore marker\n"
        "agent: opencode\n"
        "depends_on: [AAA23456]\n"
        "```"
    )

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        del spec
        if index == 0:
            return [(0.3, StreamEvent(kind="final", final_text=parent_final))]
        if index == 1:
            raw = json.dumps({"gate_question": "which env?"})
            return [(0.0, StreamEvent(kind="system", raw=raw))]
        return [(0.0, StreamEvent(kind="final", final_text="child done after answer"))]

    runs: list[dict] = []
    adapter = _Sc5LoopAdapter(runs, planner)
    loop = TaskLoop(
        cfg,
        manager,
        bus=bus,
        adapter_factory=lambda agent: adapter,
        router=_ScMarkerRouter({"child chore marker": "gate"}),
        scheduler_sink=None,
    )
    loop._store = store
    brain = RouterBrain(
        cfg, model=make_fake_model([{"agent": "opencode", "task_title": "composed chain"}])
    )
    app = create_app(cfg, bus, manager, router_brain=brain, adapters={}, taskloop_=loop)
    observer = bus.subscribe("sc5-observer")
    observed: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        loop.start()
        helper = asyncio.create_task(
            _sc5_force_dep_wait(loop, store, parent_id, observed)
        )
        try:
            yield
        finally:
            helper.cancel()
            await asyncio.gather(helper, return_exceptions=True)
            await loop.stop()

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        _sc_wait_state_sync(store, parent_id, "DONE")
        assert observed.get("done") is True
        assert observed["dep_state"] == "WAITING_DEPS"
        assert observed["inbox_count"] == 1
        child_id = observed["child_id"]
        raw_child = observed["task_yaml"]
        assert raw_child["parent"] == parent_id
        assert raw_child["origin"] == "agent"
        assert raw_child["agent"] == "opencode"
        assert raw_child["depends_on"] == [parent_id]
        parent_result = (store.root / parent_id / "result.md").read_text(encoding="utf-8")
        assert "Groundwork done." in parent_result
        assert "SUBTASK_DROP" not in parent_result
        assert "dep_wait" in _sc_kinds_of(store, child_id)
        _sc_wait_state_sync(store, child_id, "WAITING_INPUT")
        gate_rows = [
            event for event in _sc_events_of(store, child_id)
            if event["kind"] == "gate_opened"
        ]
        assert gate_rows and gate_rows[-1]["data"]["question"] == "which env?"
        client.portal.call(loop.answer, child_id, "staging")
        _sc_wait_state_sync(store, child_id, "DONE")
        child_result = (store.root / child_id / "result.md").read_text(encoding="utf-8")
        assert "child done after answer" in child_result
        assert len(runs) == 3
        assert "hand chore marker" in runs[0]["prompt"]
        assert "User answer" in runs[2]["prompt"]
        assert "which env?" in runs[2]["prompt"]
        parent_events = _sc_events_of(store, parent_id)
        drops = [event for event in parent_events if event["kind"] == "subtask_drop"]
        assert drops and drops[-1]["data"]["child"] == child_id
        assert "subtask_schedule_accepted" not in _sc_kinds_of(store, parent_id)
        states = _sc_states_from(observer, child_id)
        assert states.index("WAITING_DEPS") < states.index("WAITING_INPUT")
        ledger = read_jsonl(Path(cfg.storage.data_dir) / "state" / "runs.jsonl")
        assert any(
            row.get("task_id") == child_id and row.get("state") == "RUNNING"
            for row in ledger
        )
        for task_id in (parent_id, child_id):
            assert any(
                row.get("task_id") == task_id and row.get("state") == "DONE"
                for row in ledger
            )


# ----------------------------------------------------------------------
# Scenario 6: cron drop via the fake clock (scheduler -> loop -> DONE)
# ----------------------------------------------------------------------
class _Sc6Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self, tz=None) -> datetime:
        return self._now if tz is None else self._now.astimezone(tz)


class _Sc6DT(datetime):
    _clock: _Sc6Clock | None = None

    @classmethod
    def now(cls, tz=None) -> datetime:
        if cls._clock is None:
            return datetime.now(tz)
        return cls._clock.now(tz)


def _sc6_b_patch(clock: _Sc6Clock) -> tuple:
    del clock
    return (
        mock.patch.object(_sc6_base_ns, "datetime", _Sc6DT),
        mock.patch.object(_sc6_interval_ns, "datetime", _Sc6DT),
        mock.patch.object(_sc6_cron_ns, "datetime", _Sc6DT),
        mock.patch.object(_sc6_date_ns, "datetime", _Sc6DT),
        mock.patch.object(_sc6_ex_ns, "datetime", _Sc6DT),
    )


async def _sc6_spin_until(predicate, attempts: int = 100, tick: float = 0.05) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(tick)
    return predicate()


async def test_scenario6_cron_drop_fake_clock_to_done(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    del tmp_volume
    cfg = cfg.model_copy(
        update={
            "tasks": cfg.tasks.model_copy(
                update={"poll_seconds": 0.05, "max_concurrent": 1}
            )
        }
    )
    boot = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
    clock = _Sc6Clock(boot)
    patches = _sc6_b_patch(clock)
    _Sc6DT._clock = clock
    child_id = ""
    engine = None
    try:
        for patcher in patches:
            patcher.start()
        engine = SchedulerEngine(cfg, bus=None, clock=clock)
        await engine.restore()
        await engine.start()
        await engine.add_job(
            {
                "id": "CRON23456",
                "kind": "drop_task",
                "description": "cron dropped chore marker",
                "schedule": {"type": "cron", "expr": "* * * * * *"},
            },
            persist=True,
        )
        jobs = engine.list_jobs()
        assert len(jobs) == 1 and jobs[0]["schedule"] == {"type": "cron"}
        ledger = Path(cfg.storage.data_dir) / "state" / "scheduler_runs_ledger.jsonl"
        clock.advance(2)
        engine._sched.wakeup()
        assert await _sc6_spin_until(lambda: len(read_jsonl(ledger)) == 1)
        rows = read_jsonl(ledger)
        assert rows[0]["outcome"] == "fired"
        assert rows[0]["job_kind"] == "drop_task"
        await engine.stop()
        store = TaskStore(cfg)
        children = [
            entry for entry in store.list_inbox()
            if (yaml.safe_load(
                (store.inbox / entry / "task.yaml").read_text(encoding="utf-8")
            ).get("origin") == "scheduler")
            and (yaml.safe_load(
                (store.inbox / entry / "task.yaml").read_text(encoding="utf-8")
            ).get("description") == "cron dropped chore marker")
        ]
        assert len(children) == 1
        child_id = children[0]
    finally:
        if engine is not None:
            await engine.stop()
        for patcher in reversed(patches):
            try:
                patcher.stop()
            except RuntimeError:
                pass
        _Sc6DT._clock = None
    assert child_id

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        del index
        if "cron dropped chore marker" in spec.prompt:
            return [(0.0, StreamEvent(kind="final", final_text="cron chore done"))]
        return [(0.0, StreamEvent(kind="final", final_text="unexpected"))]

    bus = Bus()
    runs: list[dict] = []
    adapter = _Sc5LoopAdapter(runs, planner)
    loop = TaskLoop(
        cfg,
        SessionManager(cfg, bus=bus),
        bus=bus,
        adapter_factory=lambda agent: adapter,
        router=_ScMarkerRouter(),
    )
    loop.start()
    try:
        await _sc_wait_state(loop._store, child_id, "DONE")
        result = (loop._store.root / child_id / "result.md").read_text(encoding="utf-8")
        assert "cron chore done" in result
        assert loop._store.read_state(child_id)["state"] == "DONE"
    finally:
        await loop.stop()
    assert len(runs) == 1


# ----------------------------------------------------------------------
# Scenario 7: faithful-relay superset audit including the Discord leg
# ----------------------------------------------------------------------
async def test_scenario7_faithful_relay_discord_superset_audit(
    tmp_volume: Path, cfg: HarnessConfig
) -> None:
    del tmp_volume
    cfg = cfg.model_copy(
        update={"discord": cfg.discord.model_copy(update={"chunk_ms": 1.0})}
    )
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    brain = RouterBrain(
        cfg, model=make_fake_model([{"agent": "hermes", "task_title": "relay audit"}])
    )
    final_text = "relay audit final"
    hermes = ScriptedAdapter(
        [
            StreamEvent(kind="chunk", text="r1 note"),
            StreamEvent(kind="chunk", text="r2 note"),
            StreamEvent(
                kind="tool", tool_brief="grep summary", tool_name="grep"
            ),
            StreamEvent(kind="final", final_text=final_text),
        ],
        delay_s=0.05,
    )
    opencode = ScriptedAdapter([StreamEvent(kind="final", final_text="unused")], delay_s=0.0)
    client = FakeBotClient()
    channel = client.text_channel(5101)
    bot = DiscordBot(
        cfg,
        bus,
        manager,
        client=client,
        brain=brain,
        adapters={"hermes": hermes, "opencode": opencode},
    )
    await bot.start()
    try:
        message = make_message(
            client,
            "audit the relay",
            channel=channel,
            user=FakeUser(4321, "alice"),
            member=FakeMember(4321, roles=[]),
        )
        await bot.on_message(message)
        await bot.wait_turns()
        for _ in range(4):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)

        session = await manager.get_or_create(channel_handle=f"discord:{channel.id}")
        assert session.state == "idle"
        sends = client.sent_to[channel.id]
        edits = [text for _, text in client.edits]
        visible = sends + edits
        records = transcript_records(cfg, session.id)
        chunks = [
            row["data"]["text"] for row in records if row.get("kind") == "agent_chunk"
        ]
        finals = [
            row["data"]["text"] for row in records if row.get("kind") == "agent_final"
        ]
        route_rows = [row for row in records if row.get("kind") == "router"]
        assert route_rows
        route_line = f"[routed to hermes] {route_rows[0]['data']['task_title']}"
        carried = [*chunks, *finals, route_line]
        stream_text = route_line + "".join(chunks)
        for text in sends + edits:
            if not text.strip():
                continue
            assert text in stream_text or text in final_text or text == final_text
        assert route_line in sends
        for carried_text in [*chunks, *finals]:
            assert any(
                carried_text == text or carried_text in text for text in visible
            )
        tool_rows = [text for text in chunks if text.startswith("[tool]")]
        assert tool_rows and "grep summary" in tool_rows[0]
        assert any(tool_rows[0] in text for text in visible)
        assert client.edits and client.edits[-1][1] == finals[-1]
        assert finals[-1] == final_text
        assert carried
        assert all("[run failed]" not in text for text in visible)
    finally:
        await bot.stop()
