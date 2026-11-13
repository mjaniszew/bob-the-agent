"""Discord bot adapter tests (plan phase R7, TEST_PLAN row 14 parity) — the
parity path driven entirely through the zero-network fakes (no gateway, no
LLM, no disk network I/O): allowlist matrix (R-6), /new (thread + fallback +
busy refusal), /status (disk-built, honest), gate answers (bus publish IS the
contract), edit-in-place coalescing, >1900-char final chunking, failed finals
("[run failed] ..." parity row 9) and the Q4 home-channel notices. The spawn
disabled paths run without touching the real ``import discord`` (the real
client path is a smoke-test duty, not unit-tested here)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from harness.adapters.base import RunSpec, StreamEvent
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.gateway.discord_bot import DiscordBot, spawn_bot
from harness.persist import read_jsonl
from harness.router import RouterBrain
from harness.session_manager import SessionManager
from tests.mocks.discord_fakes import (
    FakeBotClient,
    FakeMember,
    FakeRole,
    FakeUser,
    make_message,
)
from tests.mocks.router_model import make_fake_model


class ScriptedAdapter:
    """Gateway-parity scripted adapter (local copy — tests stay independent
    of ``tests.test_gateway_app``): replays fixed StreamEvents, one per
    ``delay_s`` seconds, recording the RunSpecs it received."""

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


def transcript_records(cfg: HarnessConfig, session_id: str) -> list[dict]:
    return read_jsonl(
        Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl"
    )


def session_dir_count(manager: SessionManager) -> int:
    root = manager.sessions_root
    if not root.is_dir():
        return 0
    return sum(1 for entry in root.iterdir() if entry.is_dir())


def drain_probe(queue: asyncio.Queue) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def settle(chunk_ms: float, *, margin_s: float = 0.03) -> None:
    """Deterministic drain: pump ticks, then one real wait longer than the
    coalescing window so every pending edit task has fired."""
    for _ in range(4):
        await asyncio.sleep(0)
    await asyncio.sleep(chunk_ms / 1000 + margin_s)


async def _make_bot(
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
    events: list[StreamEvent] | None = None,
    *,
    outputs: list | None = None,
    users: str | None = None,
    roles: str | None = None,
    home: str | None = None,
    chunk_ms: float = 1.0,
    allow_threads: bool = True,
    delay_s: float = 0.15,
    guild_channel_id: int = 5100,
) -> tuple[DiscordBot, Bus, SessionManager, ScriptedAdapter, FakeBotClient]:
    """Fresh bot over the R-3 seams: fake router model + scripted adapter +
    FakeBotClient (six-method shim). cfg.discord is model_copy'd per test;
    env allowlist vars are set per ``users``/``roles`` (None -> cleared)."""
    for name in ("DISCORD_ALLOWED_USERS", "DISCORD_ALLOWED_ROLES"):
        monkeypatch.delenv(name, raising=False)
    if users is not None:
        monkeypatch.setenv("DISCORD_ALLOWED_USERS", users)
    if roles is not None:
        monkeypatch.setenv("DISCORD_ALLOWED_ROLES", roles)
    updates: dict = {"chunk_ms": chunk_ms}
    if home is not None:
        updates["home_channel_id"] = home
    discord = cfg.discord.model_copy(update=updates)
    cfg = cfg.model_copy(update={"discord": discord})
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    brain = RouterBrain(cfg, model=make_fake_model(outputs or [
        {"agent": "opencode", "task_title": "discord turn"}
    ]))
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
    client = FakeBotClient()
    guild = client.text_channel(guild_channel_id)
    guild.allow_threads = allow_threads
    bot = DiscordBot(cfg, bus, manager, client=client, brain=brain, adapters={
        "opencode": adapter,
        "hermes": adapter,
    })
    await bot.start()
    return bot, bus, manager, adapter, client


async def test_spawn_disabled_without_token(
    cfg: HarnessConfig, caplog: pytest.LogCaptureFixture
) -> None:
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    with caplog.at_level(logging.INFO):
        assert await spawn_bot(cfg, bus, manager) is None
        assert any("discord bot disabled" in record.getMessage() for record in caplog.records)
        discord_off = cfg.discord.model_copy(update={"enabled": False})
        cfg_off = cfg.model_copy(update={"discord": discord_off})
        caplog.clear()
        assert await spawn_bot(cfg_off, bus, manager) is None
    assert any("discord bot disabled" in record.getMessage() for record in caplog.records)


async def test_allowlist_matrix(
    cfg: HarnessConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot, bus, manager, adapter, client = await _make_bot(
        cfg, monkeypatch, users="1234", roles="707"
    )
    with caplog.at_level(logging.INFO, logger="harness.gateway.discord"):
        chan = client.text_channel(501)
        allowed = make_message(
            client,
            "build the widget",
            channel=chan,
            user=FakeUser(1234),
            member=FakeMember(1234, roles=[FakeRole(88)]),
        )
        async with bus.subscriber("probe-allowed") as queue:
            await bot.on_message(allowed)
            assert session_dir_count(manager) == 1
            session = await manager.get_or_create(channel_handle=f"discord:{chan.id}")
            records = transcript_records(cfg, session.id)
            assert any(
                record["kind"] == "user" and record["data"]["text"] == "build the widget"
                for record in records
            )
            assert any(
                topic == "reply.chunk" for topic, _payload in drain_probe(queue)
            )
        await bot.wait_turns()  # the allowed turn drains before the probe swaps

        async with bus.subscriber("probe-denied") as queue:
            denied = make_message(
                client, "sneaky", channel=chan, user=FakeUser(9999, "mallory")
            )
            sent_before = len(client.sent)
            runs_before = len(adapter.specs)
            await bot.on_message(denied)
            assert session_dir_count(manager) == 1  # no session created
            assert drain_probe(queue) == []  # no bus emission at all
            assert len(client.sent) == sent_before  # silent: no reply, no ack
            assert len(adapter.specs) == runs_before  # no adapter run

            # role-only member (user not in the users list, roles member match)
            role_chan = client.text_channel(502)
            role_msg = make_message(
                client,
                "role path",
                channel=role_chan,
                user=FakeUser(4242, "roles-only"),
                member=FakeMember(4242, roles=[FakeRole(707)]),
            )
            await bot.on_message(role_msg)
            assert session_dir_count(manager) == 2
            await bot.wait_turns()
            assert any(
                topic == "reply.chunk" for topic, _payload in drain_probe(queue)
            )

            # DM (member None) with a role allowlist configured -> honest denial
            dm = client.dm(8801)
            dm_msg = make_message(client, "dm hello", channel=dm, user=FakeUser(4242))
            sent_before = len(client.sent)
            await bot.on_message(dm_msg)
            assert session_dir_count(manager) == 2
            assert len(client.sent) == sent_before
            assert any(
                "user-id allowlist only" in record.getMessage()
                for record in caplog.records
                if record.name == "harness.gateway.discord"
            )


async def test_allowlist_empty_allows_all(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(cfg, monkeypatch)
    dm = client.dm(6600)
    chan = client.text_channel(6601)
    dm_msg = make_message(client, "dm hello", channel=dm, user=FakeUser(6602))
    guild_msg = make_message(
        client, "guild hello", channel=chan, user=FakeUser(6603),
        member=FakeMember(6603, roles=[FakeRole(1)]),
    )
    await bot.on_message(dm_msg)
    await bot.on_message(guild_msg)
    assert session_dir_count(manager) == 2
    await bot.wait_turns()


async def _guild_turn(bot: DiscordBot, client: FakeBotClient, cid: int, uid: int, text: str):
    """Send one guild message through the allow-all path and return the session."""
    message = make_message(
        client, text, channel=client.channels[cid], user=FakeUser(uid),
        member=FakeMember(uid, roles=[]),
    )
    await bot.on_message(message)
    await bot.wait_turns()
    await settle(getattr(bot._cfg.discord, "chunk_ms", 1.0))
    return await bot._manager.get_or_create(channel_handle=f"discord:{cid}")


async def test_dm_turn_roundtrip(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    dm = client.dm(8800)
    dm_msg = make_message(client, "hello there", channel=dm, user=FakeUser(8801, "kevin"))
    await bot.on_message(dm_msg)
    session = await manager.get_or_create(channel_handle=f"discord:{dm.id}")
    assert session.channel == f"discord:{dm.id}"
    await bot.wait_turns()
    await settle(0.001)
    records = transcript_records(cfg, session.id)
    final_records = [record for record in records if record.get("kind") == "agent_final"]
    assert final_records and final_records[-1]["data"]["text"] == "done"
    sends = client.sent_to[dm.id]
    assert any("[routed to opencode] discord turn" in text for text in sends)
    assert client.edits and client.edits[-1][1] == "done"


async def test_new_thread_and_session(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    chan = client.text_channel(5200)
    session_a = await _guild_turn(bot, client, 5200, 5201, "first")
    assert session_a.state == "idle"
    await _guild_turn(bot, client, 5200, 5201, "hello again")
    await bot.on_message(make_message(client, "/new", channel=chan, user=FakeUser(5201)))
    assert client.channels[5200].threads_created and len(client.channels[5200].threads_created) == 1
    thread = client.channels[5200].threads_created[0]
    assert "🆕 conversation started" in client.sent_to[thread.id]
    session_b = await manager.get_or_create(channel_handle=f"discord:{thread.id}")
    assert session_b.id != session_a.id
    assert session_b.channel == f"discord:{thread.id}"
    archived = await manager.get_or_create(session_a.id)
    assert archived.archived is True
    records = transcript_records(cfg, session_a.id)
    assert records[-1]["kind"] == "state_change" and records[-1]["state"] == "archived"
    bot_records = transcript_records(cfg, session_b.id)
    assert any(
        record["kind"] == "note" and record["data"]["prior"] == session_a.id
        for record in bot_records
    )


async def test_new_falls_back_without_threads(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(
        cfg, monkeypatch, chunk_ms=1.0, allow_threads=False
    )
    chan = client.channels[5100]
    assert chan.allow_threads is False
    session_a = await _guild_turn(bot, client, 5100, 5101, "first")
    await bot.on_message(make_message(client, "/new", channel=chan, user=FakeUser(5101)))
    assert chan.threads_created == []  # no thread support on this channel
    assert client.sent_to[chan.id][-1] == "🆕 conversation started"
    session_b = await manager.get_or_create(channel_handle=f"discord:{chan.id}")
    assert session_b.id != session_a.id
    assert session_b.channel == f"discord:{chan.id}"
    archived = await manager.get_or_create(session_a.id)
    assert archived.archived is True


async def test_new_refuses_busy(cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    chan = client.channels[5100]
    session = await _guild_turn(bot, client, 5100, 5101, "start work")
    assert session.state == "idle"
    await manager.move(session, "routing")  # simulate a busy turn
    before = session_dir_count(manager)
    await bot.on_message(make_message(client, "/new", channel=chan, user=FakeUser(5101)))
    await settle(0.001)
    assert "busy" in client.sent_to[chan.id][-1]
    after = await manager.get_or_create(session.id)
    assert after.archived is False
    assert session_dir_count(manager) == before


async def test_status_command(cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    bot, _bus, _manager, adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    chan = client.channels[5100]
    first = await _guild_turn(bot, client, 5100, 5101, "first")
    dm = client.dm(5103)
    await bot.on_message(
        make_message(client, "second conversation", channel=dm, user=FakeUser(5104))
    )
    await bot.wait_turns()
    await settle(0.001)
    first.archive()  # one archived + one live == two session dirs
    assert first.archived is True
    runs_before = len(adapter.specs)
    await bot.on_message(make_message(client, "/status", channel=chan, user=FakeUser(5101)))
    await settle(0.001)
    reply = client.sent_to[chan.id][-1]
    assert "harness status" in reply
    assert "sessions: 2" in reply
    assert "scheduled jobs: 0" in reply or "no scheduled jobs" in reply
    assert len(adapter.specs) == runs_before  # /status never routes (no LLM call)


async def test_gate_answer_parses_and_publishes(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot, bus, _manager, adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    chan = client.channels[5100]
    queue = bus.subscriber("probe9")
    async with queue as events_queue:
        answer = make_message(
            client, "answer TAB12 fix the flaky login test", channel=chan,
            user=FakeUser(5101),
        )
        await bot.on_message(answer)
        await settle(0.001)
        events = drain_probe(events_queue)
        gate = [payload for topic, payload in events if topic == "gate.answered"]
        assert len(gate) == 1
        assert gate[0]["task_id"] == "TAB12"
        assert gate[0]["answer"] == "fix the flaky login test"
        assert gate[0]["channel"] == str(chan.id)
        assert client.sent_to[chan.id] == ["✓ answer recorded for TAB12"]
        assert adapter.specs == []  # no adapter run on the gate path
        # negative: not a gate answer -> normal chat turn
        before = len(adapter.specs)
        chat = make_message(
            client, "answer this question without an id", channel=chan,
            user=FakeUser(5101),
        )
        await bot.on_message(chat)
        await bot.wait_turns()
        await settle(0.001)
        assert not any(
            topic == "gate.answered" for topic, _payload in drain_probe(events_queue)
        )
        assert len(adapter.specs) == before + 1


async def test_edit_in_place_coalescing(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chunk_ms=40 with the real clock (spec's deterministic coalescing test):
    6 rapid handle_chunk calls fire as three same-tick bursts 60ms apart, so
    the bursts coalesce across the 40ms windows into ONE send + a few edits —
    never one edit per chunk."""
    bot, _bus, _manager, _adapter, client = await _make_bot(
        cfg, monkeypatch, chunk_ms=40.0
    )
    chan = client.channels[5100]
    sid = "AAAA2222"
    bot._session_channels[sid] = chan.id

    async def burst(index: int) -> None:
        await asyncio.sleep(0.06 * index)
        await asyncio.gather(
            bot.handle_chunk(sid, f"t{2 * index}"),
            bot.handle_chunk(sid, f"t{2 * index + 1}"),
        )

    await asyncio.gather(*(burst(index) for index in range(3)))
    await asyncio.sleep(0.15)
    assert 2 <= len(client.edits) <= 4  # coalesced, NOT 6
    targets = {id(edit[0]) for edit in client.edits}
    assert len(targets) == 1  # one message object edited in place
    texts = [edit[1] for edit in client.edits]
    assert all(len(texts[i]) < len(texts[i + 1]) for i in range(len(texts) - 1))
    assert texts[-1] == "t0t1t2t3t4t5"
    await bot.handle_final(sid, "FINAL", failed=False)
    await settle(0.001)
    assert client.edits[-1][1] == "FINAL"
    assert id(client.edits[-1][0]) == id(client.edits[0][0])


async def test_final_chunking_max_1900(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_text = "x" * 4200
    bot, _bus, manager, _adapter, client = await _make_bot(
        cfg,
        monkeypatch,
        events=[
            StreamEvent(kind="chunk", text="a"),
            StreamEvent(kind="final", final_text=long_text),
        ],
        chunk_ms=1.0,
    )
    dm = client.dm(9500)
    await bot.on_message(make_message(client, "go long", channel=dm, user=FakeUser(9501)))
    session = await manager.get_or_create(channel_handle=f"discord:{dm.id}")
    await bot.wait_turns()
    await settle(0.001)
    sends = client.sent_to[dm.id]
    assert len(client.edits) >= 1 and len(client.edits[-1][1]) <= 1900
    remains = sends[1:]  # everything after the route line send
    assert all(len(piece) <= 1900 for piece in remains)
    assert "".join([client.edits[-1][1], *remains]) == long_text
    # final with NO prior chunks (direct handle_final): ceil(n / 1900) sends
    dm2 = client.dm(9600)
    bot._session_channels["BBBB2222"] = dm2.id
    await bot.handle_final("BBBB2222", long_text, failed=False)
    import math

    assert len(client.sent_to[dm2.id]) == math.ceil(len(long_text) / 1900)
    sid_rows = transcript_records(cfg, session.id)
    assert any(row.get("kind") == "agent_final" for row in sid_rows)


async def test_failed_final_honest(cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    bot, _bus, manager, _adapter, client = await _make_bot(
        cfg,
        monkeypatch,
        events=[StreamEvent(kind="failed", error="boom")],
        chunk_ms=1.0,
    )
    chan = client.channels[5100]
    await bot.on_message(
        make_message(client, "break it", channel=chan, user=FakeUser(5101),
                     member=FakeMember(5101, roles=[]))
    )
    session = await manager.get_or_create(channel_handle=f"discord:{chan.id}")
    await bot.wait_turns()
    await settle(0.001)
    assert client.edits and client.edits[-1][1].startswith("[run failed] boom")
    records = transcript_records(cfg, session.id)
    final_rows = [row for row in records if row.get("kind") == "agent_final"]
    assert final_rows and final_rows[-1]["data"]["text"].startswith("[run failed] boom")


async def test_home_channel_notice(
    cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _bot, bus, _manager, _adapter, client = await _make_bot(cfg, monkeypatch, chunk_ms=1.0)
    home = client.text_channel(6700)
    monkeypatch.setenv("HARNESS_DISCORD_HOME_CHANNEL", str(home.id))
    await bus.publish("gate.opened", {"task_id": "T9", "question": "need the prod URL?"})
    await bus.publish("task.state", {"task_id": "TA2", "old": "inbox", "new": "running"})
    await settle(0.001)
    assert client.sent_to[home.id] == [
        "⏸ task T9: need the prod URL?",
        "↻ task TA2: inbox → running",
    ]
    monkeypatch.delenv("HARNESS_DISCORD_HOME_CHANNEL")
    with caplog.at_level(logging.INFO, logger="harness.gateway.discord"):
        await bus.publish("gate.notify", {"task_id": "T9", "question": "again?", "kind": "open"})
        await settle(0.001)
    assert any(
        "notice dropped (no home channel configured)" in record.message
        for record in caplog.records
        if record.name == "harness.gateway.discord"
    )
    assert len(client.sent_to.get(home.id, [])) == 2  # no home-channel send after unset
