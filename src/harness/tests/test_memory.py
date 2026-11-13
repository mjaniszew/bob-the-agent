"""Tests for harness.memory (MEMORY_EDIT schema, line ops, budget, digest, audit, bus)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.blocks import parse_blocks
from harness.errors import MemoryBudgetError
from harness.memory import ApplyResult, MemoryEngine
from harness.persist import read_jsonl


class FakeBus:
    """Test double recording (topic, payload) pairs in its async publish."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


def _edit(yaml_body: str):
    """Parse one MEMORY_EDIT block out of its fenced representation."""
    return parse_blocks("```MEMORY_EDIT\n" + yaml_body + "\n```")[0]


def _seed_memory(tmp_volume: Path, text: str) -> Path:
    path = tmp_volume / "memory" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _audit_lines(tmp_volume: Path) -> list[dict]:
    return read_jsonl(tmp_volume / "state" / "memory_audit.jsonl")


# ---------------------------------------------------------------------------
# 1-2: load / fits


def test_load_missing_is_empty_and_unknown_key_rejected(cfg, tmp_volume: Path) -> None:
    engine = MemoryEngine(cfg)
    for key in ("MEMORY", "USER", "lessons"):
        assert engine.load(key) == ""
        assert engine.digest(key) == ""  # empty/missing file digests to ""
    with pytest.raises(ValueError, match="unknown file 'NOPE'"):
        engine.load("NOPE")


def test_fits_budget_borders(cfg) -> None:
    engine = MemoryEngine(cfg)
    assert engine.fits("USER", "x" * 1375)
    assert not engine.fits("USER", "x" * 1376)
    with pytest.raises(ValueError, match="unknown file 'NOPE'"):
        engine.fits("NOPE", "x")


# ---------------------------------------------------------------------------
# 3-6: add


def test_add_appends_bullet_and_audits(cfg, tmp_volume: Path) -> None:
    _seed_memory(tmp_volume, "## Preferences\n")
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: prefers dark mode"))
    assert isinstance(result, ApplyResult)
    assert result.ok and result.reason is None
    assert result.file == "MEMORY" and result.op == "add"
    assert result.before_len == 15 and result.after_len == 35
    assert (tmp_volume / "memory" / "MEMORY.md").read_text(encoding="utf-8").endswith(
        "- prefers dark mode\n"
    )
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1
    entry = audit[0]
    assert entry["file"] == "MEMORY" and entry["op"] == "add"
    assert entry["ok"] is True
    assert entry["before_len"] == 15 and entry["after_len"] == 35
    assert entry["ts"].endswith("Z")


def test_add_idempotent_second_apply_is_noop(cfg, tmp_volume: Path) -> None:
    _seed_memory(tmp_volume, "- morning standup\n")
    engine = MemoryEngine(cfg)
    body = "op: add\nfile: MEMORY\ncontent: prefers dark mode"
    assert engine.apply_edit(_edit(body)).ok
    file_path = tmp_volume / "memory" / "MEMORY.md"
    after_first = file_path.read_bytes()
    result = engine.apply_edit(_edit(body))
    assert result.ok and result.before_len == result.after_len
    assert result.reason == "duplicate"
    assert file_path.read_bytes() == after_first  # file bytes unchanged
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 2 and audit[1]["reason"] == "duplicate" and audit[1]["ok"] is True


def test_add_missing_content_rejected(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: add\nfile: MEMORY"))
    assert result.ok is False and result.reason == "missing content"
    assert engine.load("MEMORY") == "- keep me\n"  # file untouched
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1 and audit[0]["ok"] is False and audit[0]["reason"] == "missing content"


def test_budget_overflow_honest_rejection(cfg, tmp_volume: Path) -> None:
    # Seed exactly 2190 chars ending with a newline: an add of 15 chars would
    # land at 2190 + 2 + 15 + 1 = 2208 > 2200.
    _seed_memory(tmp_volume, "a" * 2189 + "\n")
    engine = MemoryEngine(cfg)
    file_path = tmp_volume / "memory" / "MEMORY.md"
    before = file_path.read_bytes()
    with pytest.raises(MemoryBudgetError) as excinfo:
        engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: \"123456789012345\""))
    assert str(excinfo.value) == "MEMORY.md budget 2200 exceeded: 2208 chars post-edit"
    assert file_path.read_bytes() == before  # byte-identical, NEVER truncated
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1
    assert audit[0]["ok"] is False and audit[0]["file"] == "MEMORY" and audit[0]["op"] == "add"
    assert audit[0]["before_len"] == 2190 and audit[0]["after_len"] == 2208
    assert "exceeded" in audit[0]["reason"]


# ---------------------------------------------------------------------------
# 7-11: replace / remove


def test_replace_first_match_multiline(cfg, tmp_volume: Path) -> None:
    _seed_memory(tmp_volume, "stale v1 alpha\nstale v2 beta\n")
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(
        _edit("op: replace\nfile: MEMORY\npattern: stale\ncontent:\n  - fresh\n  - multi")
    )
    assert result.ok and result.op == "replace"
    assert engine.load("MEMORY") == "fresh\nmulti\nstale v2 beta\n"
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1 and audit[0]["ok"] is True


def test_replace_pattern_not_found_rejected(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: replace\nfile: MEMORY\npattern: absent\ncontent: fresh"))
    assert result.ok is False and result.reason == "pattern not found"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1 and audit[0]["ok"] is False and audit[0]["reason"] == "pattern not found"


def test_replace_missing_fields_rejected(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: replace\nfile: MEMORY\ncontent: fresh"))
    assert result.ok is False and result.reason == "missing pattern"
    result = engine.apply_edit(_edit("op: replace\nfile: MEMORY\npattern: stale"))
    assert result.ok is False and result.reason == "missing content"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert [line["reason"] for line in audit] == ["missing pattern", "missing content"]


def test_remove_first_match(cfg, tmp_volume: Path) -> None:
    _seed_memory(tmp_volume, "obsolete one\nkeep me\nobsolete two\n")
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: remove\nfile: MEMORY\npattern: obsolete"))
    assert result.ok and result.op == "remove"
    assert engine.load("MEMORY") == "keep me\nobsolete two\n"
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1 and audit[0]["ok"] is True


def test_remove_pattern_not_found_rejected(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: remove\nfile: MEMORY\npattern: absent"))
    assert result.ok is False and result.reason == "pattern not found"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1 and audit[0]["ok"] is False


# ---------------------------------------------------------------------------
# 12-14: schema rejections


def test_unknown_op_rejected_and_audited_with_attempt(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: frobnicate\nfile: MEMORY\ncontent: x"))
    assert result.ok is False and result.reason == "unknown op 'frobnicate'"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1
    assert audit[0]["op"] == "frobnicate" and audit[0]["ok"] is False
    assert audit[0]["file"] == "MEMORY"


def test_unknown_file_rejected_and_audited_with_attempt(cfg, tmp_volume: Path) -> None:
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: add\nfile: DIARY\ncontent: x"))
    assert result.ok is False and result.reason == "unknown file 'DIARY'"
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1
    assert audit[0]["file"] == "DIARY" and audit[0]["ok"] is False


def test_rejected_block_reports_block_reason(cfg, tmp_volume: Path) -> None:
    after_seed = _seed_memory(tmp_volume, "- keep me\n").read_bytes()
    engine = MemoryEngine(cfg)
    block = parse_blocks("```MEMORY_EDIT\nop: [unclosed\n```")[0]
    assert not block.ok
    result = engine.apply_edit(block)
    assert result.ok is False and result.reason == block.reason
    assert result.file == "unknown" and result.op == "unknown"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_seed
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1
    assert audit[0]["op"] == "unknown" and audit[0]["file"] == "unknown" and audit[0]["ok"] is False


def test_unknown_field_rejected(cfg, tmp_volume: Path) -> None:
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: x\nwhen: now"))
    assert result.ok is False and result.reason == "unknown field 'when'"


# ---------------------------------------------------------------------------
# 15: apply_blocks ordering + tag filtering


def test_apply_blocks_filters_non_memory_tags_in_order(cfg, tmp_volume: Path) -> None:
    engine = MemoryEngine(cfg)
    blocks = parse_blocks(
        "```SKILL_DRAFT\n---\nname: x\ndescription: y\n---\nbody\n```\n"
        "```MEMORY_EDIT\nop: add\nfile: MEMORY\ncontent: ephemeral\n```\n"
        "```SUBTASK_DROP\ndescription: temp task\n```\n"
        "```MEMORY_EDIT\nop: remove\nfile: MEMORY\npattern: ephemeral\n```"
    )
    results = engine.apply_blocks(blocks)
    assert [item.op for item in results] == ["add", "remove"]
    assert all(result.ok for result in results)
    assert all(result.file == "MEMORY" for result in results)
    assert engine.load("MEMORY") == ""  # add then remove of the same line = clean
    with pytest.raises(ValueError):
        engine.apply_edit(blocks[0])  # apply_edit itself refuses non-MEMORY_EDIT tags
    audit = _audit_lines(tmp_volume)
    assert [line["op"] for line in audit] == ["add", "remove"]


def test_apply_blocks_keeps_earlier_edits_when_budget_raises(cfg, tmp_volume: Path) -> None:
    add = _edit("op: add\nfile: USER\ncontent: first note")
    overflow = _edit("op: add\nfile: USER\ncontent: " + "z" * 1360)
    engine = MemoryEngine(cfg)
    with pytest.raises(MemoryBudgetError):
        engine.apply_blocks([add, overflow])
    audit = _audit_lines(tmp_volume)
    assert [line["ok"] for line in audit] == [True, False]
    assert engine.load("USER").startswith("- first note\n")  # earlier edit stays applied


# ---------------------------------------------------------------------------
# 16-17: digest projections


def test_digest_takes_six_bullets_clamped(cfg, tmp_volume: Path) -> None:
    lines = ["PROSE: the preamble paragraph"]
    lines += ["- " + "y" * 118 for _ in range(10)]  # each bullet exactly 120 chars
    lines.append("PROSE: the closing paragraph")
    assert all(len(line) == 120 for line in lines[1:11])
    _seed_memory(tmp_volume, "\n".join(lines) + "\n")
    engine = MemoryEngine(cfg)
    digest = engine.digest("MEMORY")
    assert len(digest) <= 600
    digest_lines = digest.split("\n")
    assert len(digest_lines) == 6
    assert all(len(line) <= 100 for line in digest_lines)
    assert "PROSE" not in digest  # prose lines excluded


def test_digest_falls_back_to_prose_lines(cfg, tmp_volume: Path) -> None:
    lines = ["paragraph" + "p" * 100 + str(i) for i in range(4)]
    lines += ["short line" + str(i) for i in range(8)]
    _seed_memory(tmp_volume, "\n".join(lines) + "\n")
    engine = MemoryEngine(cfg)
    digest = engine.digest("MEMORY")
    digest_lines = digest.split("\n")
    assert len(digest_lines) == 6
    assert all(len(line) <= 100 for line in digest_lines)
    assert len(digest) <= 600
    assert digest_lines[0] == lines[0][:100]  # long prose line clamped


# ---------------------------------------------------------------------------
# 18: buses


async def test_memory_updated_emitted_only_on_change(cfg, tmp_volume: Path) -> None:
    bus = FakeBus()
    engine = MemoryEngine(cfg, bus=bus)
    body = "op: add\nfile: MEMORY\ncontent: prefers dark mode"
    engine.apply_edit(_edit(body))
    await asyncio.sleep(0)
    assert bus.events == [("memory.updated", {"file": "MEMORY", "op": "add"})]
    file_path = tmp_volume / "memory" / "MEMORY.md"
    after_first = file_path.read_bytes()
    engine.apply_edit(_edit(body))
    await asyncio.sleep(0)
    assert bus.events == [("memory.updated", {"file": "MEMORY", "op": "add"})]  # duplicate: none
    assert file_path.read_bytes() == after_first


def test_memory_updated_without_loop_is_noop(cfg) -> None:
    bus = FakeBus()
    engine = MemoryEngine(cfg, bus=bus)
    result = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: prefers dark mode"))
    assert result.ok
    assert bus.events == []  # sync context: no running loop, silent no-op
    assert len(read_jsonl(Path(cfg.storage.data_dir) / "state" / "memory_audit.jsonl")) == 1
