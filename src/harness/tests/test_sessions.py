"""Tests for harness.session (FSM table, transcript lines, meta.json, F10 lock)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from harness.errors import SessionFSMError
from harness.persist import read_jsonl
from harness.session import EVENTS, STATES, TRANSITIONS, Session, new_session_id

# base32 alphabet A-Z2-7 (ids draw from it; '=' padding is stripped).
_BASE32_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def _session_dir(tmp_volume: Path) -> Path:
    directory = tmp_volume / "sessions" / new_session_id()
    directory.parent.mkdir(parents=True, exist_ok=True)
    return directory


def test_new_session_id_format_and_uniqueness() -> None:
    ids = [new_session_id() for _ in range(100)]
    for session_id in ids:
        assert len(session_id) == 8
        assert "=" not in session_id
        assert set(session_id) <= _BASE32_ALPHABET
    assert len(set(ids)) == 100  # uniqueness across 100 draws


def test_fsm_table_contract() -> None:
    assert TRANSITIONS["created"] == {"routing"}
    assert TRANSITIONS["routing"] == {"agent_running", "failed"}
    assert TRANSITIONS["agent_running"] == {"relaying", "failed"}
    assert TRANSITIONS["relaying"] == {"idle", "failed"}
    assert TRANSITIONS["idle"] == {"routing", "archived"}
    assert TRANSITIONS["failed"] == {"routing"}
    assert TRANSITIONS["archived"] == set()  # terminal
    assert "agent_failed" not in STATES  # boot-recovery only, never transitioned
    assert EVENTS == {
        "user",
        "router",
        "agent_chunk",
        "agent_final",
        "system",
        "note",
        "session_link",
        "state_change",
        "handoff",
    }


def test_create_writes_transcript_and_meta(tmp_volume: Path, cfg) -> None:
    sessions_root = tmp_volume / "sessions"
    sessions_root.mkdir(parents=True)
    session_id = new_session_id()
    session = Session.create(
        sessions_root / session_id, cfg, channel_handle="discord:42", title="hello"
    )
    assert session.id == session_id
    assert session.state == "created"
    assert session.depth == 0
    assert session.parent_session is None
    assert session.archived is False
    meta = json.loads((sessions_root / session_id / "meta.json").read_text(encoding="utf-8"))
    assert set(meta) == {
        "session_id",
        "state",
        "title",
        "channel",
        "agents_refs",
        "parent_session",
        "depth",
        "created",
        "updated",
        "archived",
    }
    assert meta["session_id"] == session_id
    assert meta["state"] == "created"
    assert meta["title"] == "hello"
    assert meta["channel"] == "discord:42"
    assert meta["agents_refs"] == {}
    assert meta["parent_session"] is None
    assert meta["depth"] == 0
    assert meta["archived"] is False
    assert meta["created"] == meta["updated"]
    for key in ("created", "updated"):
        assert meta[key].endswith("Z")
        assert datetime.fromisoformat(meta[key]).tzinfo is not None
    transcript = sessions_root / session_id / "transcript.jsonl"
    assert transcript.is_file()
    assert transcript.read_text(encoding="utf-8") == ""  # born empty, present on disk


async def test_fsm_legal_happy_path_and_meta_refresh(tmp_volume: Path, cfg) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    for new_state in ("routing", "agent_running", "relaying", "idle"):
        await session.transition(new_state)
        assert session.state == new_state
    lines = read_jsonl(session.transcript_path)
    assert [line["kind"] for line in lines] == ["state_change"] * 4
    assert [line["state"] for line in lines] == ["routing", "agent_running", "relaying", "idle"]
    assert [line["data"]["from"] for line in lines] == [
        "created",
        "routing",
        "agent_running",
        "relaying",
    ]
    for line in lines:
        assert line["session_id"] == session.id
        assert line["ts"].endswith("Z")
    meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
    assert meta["state"] == "idle"
    assert meta["created"] <= meta["updated"]  # updated refreshes, created never moves


async def test_fsm_illegal_transition_raises_and_changes_nothing(
    tmp_volume: Path, cfg, caplog: pytest.LogCaptureFixture
) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    with (
        caplog.at_level(logging.WARNING, logger="harness.session"),
        pytest.raises(SessionFSMError) as excinfo,
    ):
        await session.transition("idle")
    assert session.state == "created"  # state unchanged
    assert read_jsonl(session.transcript_path) == []  # no line appended
    meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
    assert meta["state"] == "created"
    assert meta["updated"] == meta["created"]  # no meta rewrite happened
    assert excinfo.value.session_id == session.id
    assert excinfo.value.old == "created"
    assert excinfo.value.new == "idle"
    assert "illegal transition created -> idle" in str(excinfo.value)
    assert any("illegal transition" in record.message for record in caplog.records)


async def test_failed_is_resumable_by_new_routing(tmp_volume: Path, cfg) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    await session.transition("routing")
    await session.transition("failed")
    assert session.state == "failed"
    await session.transition("routing")  # a new user msg resumes the session
    assert session.state == "routing"
    assert [line["state"] for line in read_jsonl(session.transcript_path)] == [
        "routing",
        "failed",
        "routing",
    ]


async def test_archive_from_idle_sets_archived_and_rejects_other_states(
    tmp_volume: Path, cfg
) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    with pytest.raises(SessionFSMError):
        session.archive()  # created -> archived is illegal
    assert session.state == "created"
    for new_state in ("routing", "agent_running", "relaying", "idle"):
        await session.transition(new_state)
    session.archive()
    assert session.state == "archived"
    assert session.archived is True
    meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
    assert meta["state"] == "archived"
    assert meta["archived"] is True
    last_line = read_jsonl(session.transcript_path)[-1]
    assert last_line["kind"] == "state_change"
    assert last_line["state"] == "archived"
    assert last_line["data"] == {"from": "idle", "archived": True}
    with pytest.raises(SessionFSMError):
        await session.transition("routing")  # archived is terminal


async def test_append_event_unknown_kind_raises(tmp_volume: Path, cfg) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    with pytest.raises(ValueError, match="unknown transcript event kind"):
        await session.append_event("bogus", {"x": 1})
    assert read_jsonl(session.transcript_path) == []
    meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
    assert meta["updated"] == meta["created"]


async def test_append_event_line_shapes(tmp_volume: Path, cfg) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)
    await session.append_event("user", {"text": "hi"})
    await session.append_event("router", {"route": "simple"})
    await session.append_event("agent_chunk")
    await session.append_event("agent_final", {"text": "done"})
    await session.append_event("session_link", {"child": "ABCDEFGH"})
    lines = read_jsonl(session.transcript_path)
    assert [line["kind"] for line in lines] == [
        "user",
        "router",
        "agent_chunk",
        "agent_final",
        "session_link",
    ]
    for line in lines:
        assert {"ts", "session_id", "kind", "state"} <= set(line) <= {
            "ts",
            "session_id",
            "kind",
            "state",
            "data",
        }
        assert line["state"] == "created"  # the CURRENT state is stamped
        assert line["session_id"] == session.id
        assert line["ts"].endswith("Z")
        parsed = datetime.fromisoformat(line["ts"])
        assert parsed.tzinfo is not None
        assert parsed.microsecond == 0  # seconds precision
        assert parsed.microsecond == 0  # seconds precision
    assert lines[0]["data"] == {"text": "hi"}
    assert "data" not in lines[2]  # data omitted when None
    assert lines[4]["data"] == {"child": "ABCDEFGH"}


async def test_set_agent_ref_persists(tmp_volume: Path, cfg) -> None:
    directory = _session_dir(tmp_volume)
    session = Session.create(directory, cfg)
    await session.set_agent_ref("hermes", "hermes-session-1")
    await session.set_agent_ref("opencode", "oc-42")
    assert session.agents_refs == {"hermes": "hermes-session-1", "opencode": "oc-42"}
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["agents_refs"] == {"hermes": "hermes-session-1", "opencode": "oc-42"}
    reloaded = Session.load(directory, cfg)
    assert reloaded.agents_refs == {"hermes": "hermes-session-1", "opencode": "oc-42"}


def test_rename_persists(tmp_volume: Path, cfg) -> None:
    directory = _session_dir(tmp_volume)
    session = Session.create(directory, cfg)
    session.rename("quarterly report")
    assert session.title == "quarterly report"
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "quarterly report"
    assert Session.load(directory, cfg).title == "quarterly report"


async def test_concurrent_appends_serialized_f10(tmp_volume: Path, cfg) -> None:
    session = Session.create(_session_dir(tmp_volume), cfg)

    async def emit(index: int) -> None:
        await session.append_event("note", {"index": index})

    await asyncio.gather(*(emit(i) for i in range(20)))
    lines = read_jsonl(session.transcript_path)  # a successful parse of 20 lines = none torn
    assert len(lines) == 20
    assert sorted(line["data"]["index"] for line in lines) == list(range(20))
    for line in lines:
        assert line["kind"] == "note"
        assert line["session_id"] == session.id


async def test_meta_reload_restores_state_title_refs_parent_depth(tmp_volume: Path, cfg) -> None:
    directory = _session_dir(tmp_volume)
    session = Session.create(
        directory,
        cfg,
        channel_handle="web:abc",
        title="t0",
        parent_session="PARENT01",
        depth=2,
    )
    await session.transition("routing")
    await session.transition("agent_running")
    await session.set_agent_ref("hermes", "ref-1")
    session.rename("renamed")
    reloaded = Session.load(directory, cfg)
    assert reloaded.id == session.id
    assert reloaded.state == "agent_running"
    assert reloaded.title == "renamed"
    assert reloaded.channel == "web:abc"
    assert reloaded.agents_refs == {"hermes": "ref-1"}
    assert reloaded.parent_session == "PARENT01"
    assert reloaded.depth == 2
    assert reloaded.archived is False
    assert reloaded.created == session.created  # created survives reload unchanged
    assert reloaded.meta_path == directory / "meta.json"
    assert reloaded.transcript_path == directory / "transcript.jsonl"
