"""Tests for harness.session_manager (boot replay, handoff depth cap, boot wiring)."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path

import pytest

from harness.persist import read_jsonl
from harness.session import Session, new_session_id
from harness.session_manager import SessionManager

_META_KEYS = (
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
)
_TORN_JSON = b'{"ts": "2026-11-12T10:00:00Z", "kind": "agent_chunk"'


class _RecordingBus:
    """Minimal duck-typed bus: anything with ``async def publish(topic, payload)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


def _handcrafted_meta(**overrides: object) -> dict:
    meta: dict = {
        "session_id": "AAA23456",
        "state": "created",
        "title": None,
        "channel": None,
        "agents_refs": {},
        "parent_session": None,
        "depth": 0,
        "created": "2026-11-11T10:00:00Z",
        "updated": "2026-11-11T10:00:00Z",
        "archived": False,
    }
    meta.update(overrides)
    return {key: meta[key] for key in _META_KEYS}


def _handcrafted_session(
    volume: Path,
    session_id: str,
    lines: list[dict],
    *,
    meta: dict | None = None,
) -> Path:
    directory = volume / "sessions" / session_id
    directory.mkdir(parents=True)
    (directory / "transcript.jsonl").write_text(
        "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines),
        encoding="utf-8",
    )
    (directory / "meta.json").write_text(
        json.dumps(meta if meta is not None else _handcrafted_meta(session_id=session_id)) + "\n",
        encoding="utf-8",
    )
    return directory


async def _drive_to(
    manager: SessionManager, session: Session, states: tuple[str, ...]
) -> None:
    for state in states:
        await manager.move(session, state)


def test_replay_fresh_volume_zero_counts(tmp_volume: Path, cfg) -> None:
    counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 0, "recovered_failed": 0, "torn_skipped": 0}
    assert not (tmp_volume / "sessions").exists()  # replay never writes


async def test_replay_healthy_sessions_leave_files_untouched(
    tmp_volume: Path, cfg
) -> None:
    builder = SessionManager(cfg)
    idle = await builder.get_or_create()
    await _drive_to(builder, idle, ("routing", "agent_running", "relaying", "idle"))
    failed = await builder.get_or_create()
    await _drive_to(builder, failed, ("routing", "failed"))
    archived = await builder.get_or_create()
    await _drive_to(builder, archived, ("routing", "agent_running", "relaying", "idle"))
    archived.archive()

    snapshot: dict[Path, bytes] = {}
    for directory in sorted((tmp_volume / "sessions").iterdir()):
        for file in sorted(directory.iterdir()):
            snapshot[file] = file.read_bytes()
    assert len(snapshot) == 6

    counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 3, "recovered_failed": 0, "torn_skipped": 0}
    for file, content in snapshot.items():
        assert file.read_bytes() == content, f"{file} was modified by replay"


async def test_replay_marks_interrupted_sessions_agent_failed_f6(
    tmp_volume: Path, cfg
) -> None:
    builder = SessionManager(cfg)
    running = await builder.get_or_create()
    await _drive_to(builder, running, ("routing", "agent_running"))
    relaying = await builder.get_or_create()
    await _drive_to(builder, relaying, ("routing", "agent_running", "relaying"))
    before_lengths = {
        session.id: len(read_jsonl(session.transcript_path)) for session in (running, relaying)
    }

    counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 2, "recovered_failed": 2, "torn_skipped": 0}
    for session, expected_from in ((running, "agent_running"), (relaying, "relaying")):
        meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
        assert meta["state"] == "agent_failed"
        lines = read_jsonl(session.transcript_path)
        assert len(lines) == before_lengths[session.id] + 1
        note = lines[-1]
        assert note["kind"] == "note"
        assert note["state"] == "agent_failed"
        assert note["session_id"] == session.id
        assert note["ts"].endswith("Z")
        assert note["data"] == {
            "reason": "interrupted by restart before completion",
            "from_state": expected_from,
        }

    second = SessionManager(cfg).replay()  # note-then-meta order keeps boot idempotent
    assert second == {"sessions": 2, "recovered_failed": 0, "torn_skipped": 0}
    for session in (running, relaying):
        lines = read_jsonl(session.transcript_path)
        assert lines[-1]["kind"] == "note"
        meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
        assert meta["state"] == "agent_failed"


async def test_replay_heals_torn_tail_before_recovery_note(
    tmp_volume: Path, cfg
) -> None:
    directory = _handcrafted_session(
        tmp_volume,
        "CCC23456",
        [
            {
                "ts": "2026-11-11T10:00:01Z",
                "session_id": "CCC23456",
                "kind": "state_change",
                "state": "agent_running",
                "data": {"from": "routing"},
            }
        ],
        meta=_handcrafted_meta(session_id="CCC23456", state="agent_running"),
    )
    with (directory / "transcript.jsonl").open("ab") as handle:  # kill -9 mid-append
        handle.write(_TORN_JSON)

    counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 1, "recovered_failed": 1, "torn_skipped": 1}
    raw_lines = (directory / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3  # interrupted line, healed torn fragment, note
    note = json.loads(raw_lines[-1])  # the note survived as its OWN line
    assert note["kind"] == "note"
    assert note["state"] == "agent_failed"
    assert note["data"] == {
        "reason": "interrupted by restart before completion",
        "from_state": "agent_running",
    }
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["state"] == "agent_failed"
    idempotent = SessionManager(cfg).replay()
    assert idempotent == {"sessions": 1, "recovered_failed": 0, "torn_skipped": 0}


async def test_replay_torn_tail_uses_previous_line(
    tmp_volume: Path, cfg, caplog: pytest.LogCaptureFixture
) -> None:
    builder = SessionManager(cfg)
    session = await builder.get_or_create()
    await _drive_to(builder, session, ("routing", "agent_running", "relaying", "idle"))
    with session.transcript_path.open("ab") as handle:  # crash mid-append: torn tail
        handle.write(_TORN_JSON)
    before = session.transcript_path.read_bytes()

    with caplog.at_level(logging.WARNING, logger="harness.session_manager"):
        counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 1, "recovered_failed": 0, "torn_skipped": 1}
    assert any(
        "torn last line; previous state used" in record.message for record in caplog.records
    )
    assert session.transcript_path.read_bytes() == before  # nothing rewritten
    meta = json.loads(session.meta_path.read_text(encoding="utf-8"))
    assert meta["state"] == "idle"  # recovered from the previous good state_change line


async def test_replay_skips_missing_or_empty_transcript(
    tmp_volume: Path, cfg, caplog: pytest.LogCaptureFixture
) -> None:
    empty_dir = _handcrafted_session(tmp_volume, "AAA23456", [])
    ghost_dir = tmp_volume / "sessions" / "BBB23456"
    ghost_dir.mkdir(parents=True)
    (ghost_dir / "meta.json").write_text(
        json.dumps(_handcrafted_meta(session_id="BBB23456")) + "\n", encoding="utf-8"
    )
    before = {
        directory: (directory / "meta.json").read_bytes()
        for directory in (empty_dir, ghost_dir)
    }

    with caplog.at_level(logging.WARNING, logger="harness.session_manager"):
        counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 2, "recovered_failed": 0, "torn_skipped": 0}
    assert any("missing or empty transcript" in record.message for record in caplog.records)
    for directory, meta_bytes in before.items():
        assert (directory / "meta.json").read_bytes() == meta_bytes


async def test_create_child_depth_cap_and_both_side_session_links(
    tmp_volume: Path, cfg
) -> None:
    manager = SessionManager(cfg)
    root_parent = await manager.get_or_create(title="chain")
    child1 = await manager.create_child(root_parent, {"prompt": "first hop"})
    child2 = await manager.create_child(child1, {"prompt": "second hop"})
    child3 = await manager.create_child(child2, {"prompt": "third hop"})
    assert (child1.depth, child2.depth, child3.depth) == (1, 2, 3)
    assert child1.parent_session == root_parent.id
    assert child1.state == "routing"  # child handed to the router on creation
    assert child1.title == "first hop"

    # Depth cap: a 4th hop is rejected with an honest ValueError, no partial writes.
    listing_before = sorted(p.name for p in (tmp_volume / "sessions").iterdir())
    transcript_before = read_jsonl(child3.transcript_path)
    meta_before = child3.meta_path.read_bytes()
    with pytest.raises(ValueError, match="handoff depth 4 exceeds max_depth 3"):
        await manager.create_child(child3, {"prompt": "one hop too far"})
    assert sorted(p.name for p in (tmp_volume / "sessions").iterdir()) == listing_before
    assert read_jsonl(child3.transcript_path) == transcript_before
    assert child3.meta_path.read_bytes() == meta_before
    assert child3.state == "routing"

    # A still-legal hop is accepted and linked on BOTH transcripts (section 3.2).
    sibling = await manager.create_child(child1, {"prompt": "still legal at depth 2"})
    assert sibling.depth == 2
    assert sibling.parent_session == child1.id
    assert sibling.state == "routing"
    sibling_meta = json.loads(sibling.meta_path.read_text(encoding="utf-8"))
    assert sibling_meta["parent_session"] == child1.id
    assert sibling_meta["depth"] == 2
    parent_lines = read_jsonl(child1.transcript_path)
    assert any(
        line["kind"] == "session_link" and line["data"] == {"child": sibling.id}
        for line in parent_lines
    )
    child_lines = read_jsonl(sibling.transcript_path)
    assert any(
        line["kind"] == "session_link" and line["data"] == {"parent": child1.id}
        for line in child_lines
    )


async def test_get_or_create_channel_reuse_and_unknown_id(tmp_volume: Path, cfg) -> None:
    manager = SessionManager(cfg)
    first = await manager.get_or_create(channel_handle="discord:thread-9", title="ch")
    assert first.state == "created"
    again = await manager.get_or_create(channel_handle="discord:thread-9")
    assert again.id == first.id and again is first  # registry hit
    by_id = await manager.get_or_create(session_id=first.id)
    assert by_id is first

    # A fresh manager finds the live channel owner on disk (section 2.4).
    disk_scan = await SessionManager(cfg).get_or_create(channel_handle="discord:thread-9")
    assert disk_scan.id == first.id

    # An archived session no longer owns its channel: reuse creates a new one.
    doomed = await manager.get_or_create(channel_handle="discord:thread-10")
    await _drive_to(manager, doomed, ("routing", "agent_running", "relaying", "idle"))
    doomed.archive()
    reborn = await SessionManager(cfg).get_or_create(channel_handle="discord:thread-10")
    assert reborn.id != doomed.id

    with pytest.raises(ValueError, match="unknown session id"):
        await manager.get_or_create(session_id=new_session_id())
    with pytest.raises(ValueError, match="invalid session id"):
        await manager.get_or_create(session_id="../oopsies")


async def test_publish_status_emits_session_status(tmp_volume: Path, cfg) -> None:
    bus = _RecordingBus()
    manager = SessionManager(cfg, bus=bus)
    session = await manager.get_or_create()
    await manager.publish_status(session, "created", "routing")
    assert bus.events == [
        ("session.status", {"session_id": session.id, "old": "created", "new": "routing"})
    ]
    await manager.move(session, "routing")
    assert session.state == "routing"
    assert bus.events[-1] == (
        "session.status",
        {"session_id": session.id, "old": "created", "new": "routing"},
    )

    silent = SessionManager(cfg)  # no bus attached: publish_status is a no-op
    other = await silent.get_or_create()
    await silent.publish_status(other, "created", "routing")


def test_replay_counters_and_ready_line(tmp_volume: Path, cfg) -> None:
    for directory in ("AAA23456", "BBB23456", "CCC23456"):
        _handcrafted_session(
            tmp_volume,
            directory,
            [{"ts": "2026-11-11T10:00:00Z", "session_id": directory, "kind": "user", "state": "created"}],
        )
    counts = SessionManager(cfg).replay()
    assert counts == {"sessions": 3, "recovered_failed": 0, "torn_skipped": 0}
    assert SessionManager.ready_line(counts) == "harness ready - sessions=3"


def test_supervisor_serve_wires_session_manager() -> None:
    import harness.supervisor as supervisor_module

    source = inspect.getsource(supervisor_module._serve)
    assert "SessionManager" in source
    assert "replay" in source
    assert "ready_line" in source
    assert "aclose" in source  # bus closed after uvicorn exits
    assert "Bus()" in source
