"""R12a end-to-end battery, scenarios 1-4 (zero network).

Every test drives the REAL harness components (FastAPI gateway / DiscordBot,
SessionManager, persist files under ``cfg.storage.data_dir``) over the two
documented test seams only:

- the router seam is the R5 fake model OBJECT (``tests.mocks.router_model``);
- the adapter seam is a local :class:`ScriptedAdapter` (run(spec) -> async
  iterator of :class:`StreamEvent`), never a real provider or subprocess.

Scenario 5-7 land in a follow-up task: keep new tests appended below the
shared helpers / scenario-4 block.

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
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from harness.adapters.base import RunSpec, StreamEvent
from harness.blocks import parse_blocks
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.gateway.app import create_app
from harness.gateway.discord_bot import DiscordBot
from harness.persist import read_jsonl
from harness.router import RouterBrain
from harness.session_manager import SessionManager
from harness.skills import SkillsEngine
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
