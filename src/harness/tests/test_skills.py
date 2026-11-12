"""Tests for harness.skills (INDEX.md build, body loader, capture, supervisor wiring)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

import pytest

from harness.blocks import parse_blocks
from harness.skills import (
    CAPTURE_PROMPT_TEMPLATE,
    SKILL_BODY_MAX_CHARS,
    TRUNCATION_MARKER,
    SkillsEngine,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "skills"

_DEPLOY_DESCRIPTION = (
    "Deploy the Flask service to production with zero-downtime rolling restarts, "
    "health-gated traffic promotion and a documented rollback procedure"
)
_SCRAPE_BODY = "## Steps\n1. Fetch the table.\n2. Parse rows with pandas.\n3. Write CSV.\n"


class FakeBus:
    """Test double recording (topic, payload) pairs in its async publish."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


def _fixture_skills(cfg, tmp_volume: Path) -> SkillsEngine:
    shutil.copytree(FIXTURE_ROOT, tmp_volume / "skills")
    return SkillsEngine(cfg)


def _draft(name: str, description: str, body: str = "## Steps\n1. Do the thing.\n"):
    doc = f"---\nname: {name}\ndescription: {description}\n---\n" + body
    block = parse_blocks("```SKILL_DRAFT\n" + doc + "\n```")[0]
    assert block.ok, block.reason
    return block


def _skill_dirs(tmp_volume: Path) -> set[str]:
    return {entry.name for entry in (tmp_volume / "skills").iterdir() if entry.is_dir()}


# ---------------------------------------------------------------------------
# 1-5: build_index


def test_build_index_from_fixtures(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    text = engine.build_index()
    index_path = tmp_volume / "skills" / "INDEX.md"
    assert text == index_path.read_text(encoding="utf-8")
    assert len(text) <= 1500
    expected_deploy = f"- deploy-flask: {_DEPLOY_DESCRIPTION[:57]}"
    assert text == (
        "# Skill index (generated)\n"
        + expected_deploy
        + "\n"
        + "- scrape-tables: Scrape HTML tables into CSV files.\n"
    )
    assert "broken-skill" not in text  # invalid frontmatter skipped
    assert text.endswith("\n")


def test_build_index_deterministic_and_no_write_on_second_build(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    index_path = tmp_volume / "skills" / "INDEX.md"
    first = engine.build_index()
    assert index_path.read_text(encoding="utf-8") == first
    assert not list((tmp_volume / "skills").glob("*.tmp"))  # no tmp leftovers after rewrite
    engine.build_index()  # second build sees identical bytes -> no rewrite
    mtime_after_second = index_path.stat().st_mtime_ns
    assert engine.build_index() == first  # byte-identical output
    assert index_path.stat().st_mtime_ns == mtime_after_second  # unchanged -> no rewrite
    index_path.unlink()
    fourth = engine.build_index()
    assert fourth == first  # rebuild from scratch -> byte-identical again
    assert not list((tmp_volume / "skills").glob("*.tmp"))


def _scaffold_skill(skills_dir: Path, index: int) -> None:
    folder = skills_dir / f"s{index:02d}"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: s{index:02d}\ndescription: {'x' * 60}\ndummy: true\n---\nbody\n",
        encoding="utf-8",
    )


def test_build_index_budget_exceeded_honest_valueerror(cfg, tmp_volume: Path) -> None:
    skills_dir = tmp_volume / "skills"
    for i in range(25):
        _scaffold_skill(skills_dir, i)
    engine = SkillsEngine(cfg)
    with pytest.raises(ValueError) as excinfo:
        engine.build_index()
    assert re.match(
        r"skills index budget 1500 exceeded: 25 skills, \d+ chars \(prune skill descriptions\)",
        str(excinfo.value),
    )
    assert not (skills_dir / "INDEX.md").exists()  # honest cap: nothing written


def test_build_index_skips_invalid_frontmatter_with_warning(cfg, tmp_volume: Path, caplog) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    with caplog.at_level(logging.WARNING, logger="harness.skills"):
        text = engine.build_index()
    assert any("broken-skill" in record.message for record in caplog.records)
    assert "- deploy-flask: " in text and "- scrape-tables: " in text
    assert "broken-skill" not in text


def test_build_index_missing_skills_dir_is_header_only(cfg, tmp_volume: Path) -> None:
    engine = SkillsEngine(cfg)
    text = engine.build_index()
    assert text == "# Skill index (generated)\n"
    assert (tmp_volume / "skills" / "INDEX.md").read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# 6-7: get_skill_body


def test_get_skill_body_truncates_long_and_strips_frontmatter(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    body = engine.get_skill_body("deploy-flask")
    assert len(body) == SKILL_BODY_MAX_CHARS + len(TRUNCATION_MARKER)
    assert body.endswith(TRUNCATION_MARKER)
    assert "---" not in body  # no frontmatter remnants
    assert "name: deploy-flask" not in body and "description: Deploy" not in body
    assert body.startswith("# Deploy the Flask app\n")


def test_get_skill_body_short_verbatim_and_missing_empty(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    assert engine.get_skill_body("scrape-tables") == _SCRAPE_BODY
    assert TRUNCATION_MARKER not in engine.get_skill_body("scrape-tables")
    assert engine.get_skill_body("nope") == ""


# ---------------------------------------------------------------------------
# 8-12: capture


def test_capture_creates_folder_verbatim_and_rebuilds_index(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    block = _draft(
        "ship containers", "Ship containers to the registry.", "## Steps\n1. Tag the build.\n"
    )
    result = engine.capture("sess-1", block)
    assert result["slug"] == "ship-containers"
    assert result["created_new"] is True
    assert result["path"] == str(tmp_volume / "skills" / "ship-containers" / "SKILL.md")
    assert (tmp_volume / "skills" / "ship-containers" / "SKILL.md").read_text() == block.body
    index = (tmp_volume / "skills" / "INDEX.md").read_text(encoding="utf-8")
    assert "- ship-containers: Ship containers to the registry." in index
    assert "- scrape-tables: " in index  # rebuild keeps existing skills


def test_capture_slug_bumps_on_collision(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    original = (tmp_volume / "skills" / "deploy-flask" / "SKILL.md").read_bytes()
    block = _draft("deploy flask", "Another deployment skill draft.")
    result = engine.capture("sess-2", block)
    assert result["slug"] == "deploy-flask-2"
    assert result["created_new"] is False
    assert (tmp_volume / "skills" / "deploy-flask" / "SKILL.md").read_bytes() == original
    assert (tmp_volume / "skills" / "deploy-flask-2" / "SKILL.md").read_text() == block.body


def test_capture_distill_callable_replaces_body(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    calls: list[tuple[str, str]] = []

    def distill(session_id: str, body_md: str) -> str:
        calls.append((session_id, body_md))
        return body_md.replace("TODO", "DONE")

    block = _draft(
        "todo skill", "Skill distilled from a todo session.", "## Steps\n1. TODO write later.\n"
    )
    result = engine.capture("sess-3", block, distill=distill)
    assert result["slug"] == "todo-skill"  # slug still derives from the original name
    assert calls == [("sess-3", block.body)]  # invoked exactly once with the verbatim body
    written = (tmp_volume / "skills" / "todo-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert written == block.body.replace("TODO", "DONE")
    index = (tmp_volume / "skills" / "INDEX.md").read_text(encoding="utf-8")
    assert "- todo-skill: Skill distilled from a todo session." in index


def test_capture_rejects_bad_or_wrong_tag_draft(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    engine.build_index()  # ensure INDEX.md exists so "unchanged" is byte-checkable
    index_path = tmp_volume / "skills" / "INDEX.md"
    index_before = index_path.read_bytes()
    dirs_before = _skill_dirs(tmp_volume)
    bad_doc = parse_blocks("```SKILL_DRAFT\nname: x\nno frontmatter at all\n```")[0]
    assert not bad_doc.ok
    with pytest.raises(ValueError):
        engine.capture("sess-4", bad_doc)  # rejected SKILL_DRAFT block
    wrong_tag = parse_blocks("```MEMORY_EDIT\nop: add\nfile: MEMORY\ncontent: x\n```")[0]
    assert wrong_tag.ok
    with pytest.raises(ValueError):
        engine.capture("sess-4", wrong_tag)  # non-SKILL_DRAFT tag
    assert index_path.read_bytes() == index_before  # INDEX.md unchanged
    assert _skill_dirs(tmp_volume) == dirs_before  # no folder created


def test_capture_slug_sanitization_and_empty_rejection(cfg, tmp_volume: Path) -> None:
    engine = _fixture_skills(cfg, tmp_volume)
    dirs_before = _skill_dirs(tmp_volume)
    block = _draft("My Cool Skill v2!", "A skill with special chars!!")
    result = engine.capture("sess-5", block)
    assert result["slug"] == "my-cool-skill-v2"
    assert result["created_new"] is True
    assert (tmp_volume / "skills" / "my-cool-skill-v2" / "SKILL.md").exists()
    dirs_after = _skill_dirs(tmp_volume)
    assert dirs_after == dirs_before | {"my-cool-skill-v2"}
    stars = _draft('"***"', "A name that slugifies to nothing.")
    with pytest.raises(ValueError):
        engine.capture("sess-5", stars)  # slug empty -> ValueError, nothing written
    assert _skill_dirs(tmp_volume) == dirs_after


# ---------------------------------------------------------------------------
# 13-15: bus emission, prompt template, supervisor wiring


async def test_capture_emits_skill_captured(cfg, tmp_volume: Path) -> None:
    bus = FakeBus()
    engine = SkillsEngine(cfg, bus=bus)
    block = _draft("ship containers", "Ship containers to the registry.")
    result = engine.capture("sess-6", block)
    await asyncio.sleep(0)
    assert bus.events == [
        ("skill.captured", {"skill_name": "ship-containers", "path": result["path"]})
    ]


def test_capture_prompt_template_contract() -> None:
    assert "Distill this session into SKILL.md for skill '<name>'" in CAPTURE_PROMPT_TEMPLATE
    assert "```SKILL_DRAFT\n" in CAPTURE_PROMPT_TEMPLATE
    assert "name: <name>\n" in CAPTURE_PROMPT_TEMPLATE
    filled = CAPTURE_PROMPT_TEMPLATE.replace("<name>", "deploy-flask")
    assert "<name>" not in filled
    assert "skill 'deploy-flask'" in filled
    assert "name: deploy-flask" in filled


def test_supervisor_boot_skills_index_never_raises(
    cfg, tmp_volume: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.supervisor import _boot_skills_index

    _fixture_skills(cfg, tmp_volume)
    with caplog.at_level(logging.INFO):
        _boot_skills_index(cfg)
    info = [record for record in caplog.records if "skills index rebuilt" in record.message]
    assert len(info) == 1 and info[0].levelno == logging.INFO
    index_path = tmp_volume / "skills" / "INDEX.md"
    assert index_path.read_text(encoding="utf-8").startswith("# Skill index (generated)\n")

    # Budget-poisoned: a tiny index budget makes the rebuild raise -> caught warning.
    monkeypatch.setattr(cfg.memory.budgets, "index_chars", 20)
    with caplog.at_level(logging.WARNING):
        _boot_skills_index(cfg)  # returns, must not raise
    warnings = [
        record for record in caplog.records if "skills index rebuild failed" in record.message
    ]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
