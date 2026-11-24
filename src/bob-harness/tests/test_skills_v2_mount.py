"""TDD failing-first tests for the harness ``Skills`` capability mount (P5, D5).

Plan: ``docs/plans/10-pydantic-ai-v2-migration-plan.md`` (repo root) Part 2
section ``P5 - Skills (D5)``, executed on a bare ``Agent`` where the surface
is the capability itself, and over REAL ``ExecutorAdapter`` runs
(``FunctionModel``) where assembly/reach is the surface. The engine
(``bob_harness.skills.SkillsEngine``) keeps sole ownership of byte-deterministic
INDEX.md and of SKILL_DRAFT capture; the mount only READS skill folders into
the run catalog. Verified 0.73.0 surfaces this pins:

- ``Skills(directories, include, exclude, missing_directories, duplicate_names,
  unknown_names, workspace, scanner, read_files)``; ``'ignore'`` is NOT a
  legal ``missing_directories`` value — the ctor raises ``ValueError``
  (regression guard, test 3).
- Absolute library directories resolve INSIDE the ``workspace=`` backend and
  the executor passes ``workspace=LocalWorkspaceBackend(<data>/skills)`` so a
  run needs no workspace of its own for the mount (tests 1/6/8/2);
  ``get_toolset()`` is None while ``read_files=False`` and the
  ``read_skill_file`` FunctionToolset appears with True (tests 6/7).
- The deferred catalog (``info.instructions``) carries name/description/
  when_to_use only; the SKILL.md body reaches the wire solely after an
  explicit ``load_capability`` turn (test 9), and with ``read_files=False``
  no ``read_skill_file`` tool exists (test 7).
- The engine's body path is ``SkillsEngine.get_skill_body``: hard truncation
  at ``SKILL_BODY_MAX_CHARS`` (4000) with ``TRUNCATION_MARKER`` appended
  (test 7) — byte-unchanged from the pre-P5 contract.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness.skills import Skills

import bob_harness.skills as skills_module
from bob_harness.adapters import RunSpec
from bob_harness.config import load_config

FIXTURE_SKILLS = Path(__file__).parent / "fixtures" / "skills"

# The engine's INDEX.md cut is the first 57 chars of the fixture description.
_DEPLOY_DESCRIPTION_CUT = (
    "Deploy the Flask service to production with zero-downtime rolling restarts, "
    "health-gated traffic promotion and a documented rollback procedure"
)[:57]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _data_with_fixture_skills(tmp_path: Path) -> Path:
    """A data tree whose <data>/skills holds the R3 fixtures (deploy-flask,
    scrape-tables, broken-skill). INDEX.md is left ABSENT — the engine, not
    the mount, owns it; the fixture tree carries no INDEX.md."""
    data = tmp_path / "data"
    shutil.copytree(FIXTURE_SKILLS, data / "skills")
    assert not (data / "skills" / "INDEX.md").exists()
    return data


def _cfg_with_skills(
    tmp_path: Path,
    data_dir: Path,
    *,
    enabled: bool = True,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    read_files: bool = False,
    missing_directories: str = "skip",
) -> Any:
    """Config with ONLY the skills capability enabled (single-capability runs
    keep the FunctionModel probes deterministic)."""
    lines = [
        f"storage:\n  data_dir: '{data_dir}'\n",
        "harness:\n",
        "  memory:\n    enabled: false\n",
        f"  skills:\n    enabled: {str(enabled).lower()}\n",
        f"    include: {json.dumps(include or [])}\n",
        f"    exclude: {json.dumps(exclude or [])}\n",
        f"    read_files: {str(read_files).lower()}\n",
        f"    missing_directories: '{missing_directories}'\n",
        "  ask_user:\n    enabled: false\n",
        "  subagents:\n    enabled: false\n",
        "  delegation:\n    enabled: false\n",
    ]
    cfg_path = tmp_path / "skills-mount.yaml"
    cfg_path.write_text("".join(lines), encoding="utf-8")
    return load_config(str(cfg_path))


def _collect_skill_lines(catalog: str) -> list[str]:
    """The deferred-capability catalog bullet lines (one per mounted skill)."""
    in_block = False
    lines: list[str] = []
    for line in catalog.splitlines():
        if line.startswith("<pyai:context"):
            in_block = True
        elif line.startswith("</pyai:context>"):
            in_block = False
        elif in_block and line.startswith("- "):
            lines.append(line)
    return lines


def _catalog_of(info: AgentInfo) -> str:
    """The run instruction text the catalog was rendered into."""
    return info.instructions or ""


def _tool_names(info: AgentInfo) -> list[str]:
    return sorted(tool.name for tool in (info.function_tools or []))


def _mount_capabilities(cfg: Any) -> list[Any]:
    from bob_harness.adapters.executor import assemble_capabilities

    return [
        cap
        for cap in assemble_capabilities(cfg, workdir=None)
        if type(cap).__name__ == "Skills"
    ]


def _catalog_names(catalog: str) -> list[str]:
    return [
        line.split(":", 1)[0].removeprefix("- ")
        for line in _collect_skill_lines(catalog)
    ]


async def _catalog_names_of_cfg(cfg: Any, tmp_path: Path) -> list[str]:
    """Catalog slugs of one mount over one run (bare agent, FunctionModel)."""
    (cap,) = _mount_capabilities(cfg)
    workdir = tmp_path / "run-ws"
    workdir.mkdir(exist_ok=True)
    catalogs: list[str] = []

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        catalogs.append(_catalog_of(info))
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(_model), name="executor", capabilities=[cap])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        await agent.run("hi", workspace=LocalWorkspaceBackend(workdir))
    return _catalog_names(catalogs[0])


# ---------------------------------------------------------------------------
# 1. Mount reads the EXISTING SKILL.md folders (frontmatter discovery)
# ---------------------------------------------------------------------------


async def test_capability_mounts_existing_folders(tmp_path: Path) -> None:
    """P5-1: Skills catalogs name/description (and when_to_use) from the
    existing <data_dir>/skills folders; the run's tool surface gets
    load_capability; the FULL fixture description survives (not the engine's
    57-char INDEX cut)."""
    data = _data_with_fixture_skills(tmp_path)
    full_description = (
        "Deploy the Flask service to production with zero-downtime rolling restarts, "
        "health-gated traffic promotion and a documented rollback procedure"
    )
    assert len(full_description) > 57  # the engine's INDEX cut would hide this

    cfg = _cfg_with_skills(tmp_path, data)
    caps = _mount_capabilities(cfg)
    assert len(caps) == 1
    cap = caps[0]
    assert type(cap).__name__ == "Skills"
    assert cap.directories == (data / "skills",)

    catalogs: list[str] = []
    tools: list[list[str]] = []

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        catalogs.append(_catalog_of(info))
        tools.append(_tool_names(info))
        return ModelResponse(parts=[TextPart("ok")])

    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    agent = Agent(FunctionModel(_model), name="executor", capabilities=[cap])
    await agent.run("hello", workspace=LocalWorkspaceBackend(workdir))

    names = _catalog_names(catalogs[0])
    assert "deploy-flask" in names and "scrape-tables" in names
    assert "broken-skill" not in names  # invalid frontmatter skipped deterministically
    deploy_line = next(line for line in _collect_skill_lines(catalogs[0]) if line.startswith("- deploy-flask:"))
    assert full_description in deploy_line  # full description, not the 57-char cut
    assert "load_capability" in tools[0]


# ---------------------------------------------------------------------------
# 2-3. missing_directories tolerance
# ---------------------------------------------------------------------------


async def test_missing_directories_skip_on_fresh_tree(tmp_path: Path) -> None:
    """P5-2: 'skip' boots on an EMPTY/fresh data dir — no skills/ directory
    at all — through the REAL adapter path (assemble -> executor run)."""
    data = tmp_path / "data"
    data.mkdir()  # fresh tree: NO skills dir
    cfg = _cfg_with_skills(tmp_path, data, missing_directories="skip")
    caps = _mount_capabilities(cfg)
    assert len(caps) == 1
    cap = caps[0]
    assert cap.missing_directories == "skip"

    from bob_harness.adapters.executor import ExecutorAdapter

    adapter = ExecutorAdapter(cfg)

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("fresh-ok")])

    # workdir=None: the mount carries its own workspace= backend (D5 glue)
    events = [
        event
        async for event in adapter.run(
            RunSpec(prompt="fresh tree", model=FunctionModel(_model))
        )
    ]
    assert [e.kind for e in events] == ["started", "final"]
    assert events[-1].final_text == "fresh-ok"

    # ... and the bare capability boots empty over the root the glue created
    # at assembly (D4-precedent behavior: workspace roots must exist, so the
    # mount creates <data>/skills when absent)
    assert (data / "skills").is_dir()
    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    catalogs: list[str] = []

    def _model2(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        catalogs.append(_catalog_of(info))
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(_model2), name="executor", capabilities=[caps[0]])
    await agent.run("hi", workspace=LocalWorkspaceBackend(workdir))
    assert _collect_skill_lines(catalogs[0]) == []


def test_missing_directories_ignore_is_valueerror(tmp_path: Path) -> None:
    """P5-3: 'ignore' raises ValueError at ctor time (0.73.0 enum: exactly
    'error'|'skip'; regression guard)."""
    data = _data_with_fixture_skills(tmp_path)
    with pytest.raises(ValueError, match="missing_directories"):
        Skills(
            directories=[data / "skills"],
            missing_directories="ignore",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# 4. INDEX.md stays engine-owned (D5 invariant)
# ---------------------------------------------------------------------------


async def test_index_md_stays_engine_owned(tmp_path: Path) -> None:
    """P5-4: with the capability mounted over the SAME root, INDEX.md bytes
    are exactly what SkillsEngine produces — and a capability read does NOT
    rewrite/create it (engine-own; R3 seed untouched)."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data)
    engine = skills_module.SkillsEngine(cfg)
    generated = engine.build_index()
    assert generated == (
        "# Skill index (generated)\n"
        + f"- deploy-flask: {_DEPLOY_DESCRIPTION_CUT}\n"
        + "- scrape-tables: Scrape HTML tables into CSV files.\n"
    )
    index_before = (data / "skills" / "INDEX.md").read_bytes()

    caps = _mount_capabilities(cfg)
    assert len(caps) == 1
    workdir = tmp_path / "run-ws"
    workdir.mkdir()

    catalogs: list[str] = []

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        catalogs.append(_catalog_of(info))
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(_model), name="executor", capabilities=caps)
    await agent.run("hi", workspace=LocalWorkspaceBackend(workdir))
    # the mounted catalog carries the skills AND stays clear of the index
    # file's own header (INDEX.md is not a skill, and the mount never mirrors
    # engine bytes into the run)
    assert _catalog_names(catalogs[0]) == ["deploy-flask", "scrape-tables"]
    assert "Skill index (generated)" not in catalogs[0]
    assert (data / "skills" / "INDEX.md").read_bytes() == index_before


# ---------------------------------------------------------------------------
# 5. Capture loop stays engine-owned (suffixes + event)
# ---------------------------------------------------------------------------


def _draft(name: str, description: str) -> Any:
    doc = f"---\nname: {name}\ndescription: {description}\n---\n## Steps\n1. Do it.\n"
    from bob_harness.blocks import parse_blocks

    block = parse_blocks("```SKILL_DRAFT\n" + doc + "\n```")[0]
    assert block.ok, block.reason
    return block


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


async def test_capture_suffixes_and_event(tmp_path: Path) -> None:
    """P5-5: -2/-3 slug suffixes + skill.captured bus event unchanged."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data)
    assert len(_mount_capabilities(cfg)) == 1  # mount over the same root the engine writes

    bus = _FakeBus()
    engine = skills_module.SkillsEngine(cfg, bus=bus)
    first = engine.capture("s1", _draft("deploy flask", "Another deploy draft."))
    second = engine.capture("s2", _draft("deploy flask", "Third deploy draft."))
    third = engine.capture("s3", _draft("deploy flask", "Fourth deploy draft."))
    # deploy-flask already exists in the fixture tree: -2/-3/-4 bumps all
    # collide (created_new False — the ORIGINAL folder is never overwritten)
    assert first["slug"] == "deploy-flask-2" and first["created_new"] is False
    assert second["slug"] == "deploy-flask-3" and second["created_new"] is False
    assert third["slug"] == "deploy-flask-4" and third["created_new"] is False
    index = (data / "skills" / "INDEX.md").read_text(encoding="utf-8")
    assert "- deploy-flask-2: Another deploy draft." in index
    assert "- deploy-flask-3: Third deploy draft." in index
    await asyncio.sleep(0)  # let the fire-and-forget skill.captured emits land
    # the event payload carries the SLUG of the frontmatter name
    # ('deploy flask' -> 'deploy-flask'), not the bumped folder slug — engine
    # contract — and exactly one event per capture (three captures here).
    events = [(t, dict(p)) for t, p in bus.events]
    assert events == [
        ("skill.captured", {"skill_name": "deploy-flask", "path": first["path"]}),
        ("skill.captured", {"skill_name": "deploy-flask", "path": second["path"]}),
        ("skill.captured", {"skill_name": "deploy-flask", "path": third["path"]}),
    ]


# ---------------------------------------------------------------------------
# 6. Executor gets the skill toolset (assembly wiring, cfg-sourced)
# ---------------------------------------------------------------------------


def test_executor_gets_skill_toolset(tmp_path: Path) -> None:
    """P5-6: assemble_capabilities() adds Skills(directories=[<data>/skills], ...)
    reading include/exclude/read_files/missing_directories from
    cfg.harness.skills; get_toolset() is None while read_files is False and
    the read_skill_file FunctionToolset appears when True."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data, read_files=False)
    caps = _mount_capabilities(cfg)
    assert len(caps) == 1
    (cap,) = caps
    assert type(cap).__name__ == "Skills"
    assert cap.read_files is False
    assert cap.get_toolset() is None  # no read_skill_file tool without read_files

    rf_cfg = _cfg_with_skills(tmp_path, data, read_files=True)
    (rf_cap,) = _mount_capabilities(rf_cfg)
    assert rf_cap.read_files is True
    toolset = rf_cap.get_toolset()
    assert toolset is not None
    assert list(toolset.tools.keys()) == ["read_skill_file"]


# ---------------------------------------------------------------------------
# 7. read_files=False: body access stays the explicit engine path
# ---------------------------------------------------------------------------


def test_read_files_false_no_body_surface(tmp_path: Path) -> None:
    """P5-7: without read_files the model NEVER gets a body tool — the engine's
    get_skill_body (hard-truncate 4000 + marker) stays the explicit body path,
    byte-unchanged, while the mount catalogs the description only."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data, read_files=False)
    (cap,) = _mount_capabilities(cfg)
    assert cap.get_toolset() is None

    engine = skills_module.SkillsEngine(cfg)
    long_body = "L" * 4200
    skill_dir = data / "skills" / "long-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: long-skill\ndescription: A skill with a long body.\n---\n" + long_body,
        encoding="utf-8",
    )
    body = engine.get_skill_body("long-skill")
    assert len(body) == 4000 + len(skills_module.TRUNCATION_MARKER)
    assert body.endswith(skills_module.TRUNCATION_MARKER)
    short = engine.get_skill_body("scrape-tables")
    assert short == "## Steps\n1. Fetch the table.\n2. Parse rows with pandas.\n3. Write CSV.\n"
    assert short.endswith("\n")


# ---------------------------------------------------------------------------
# 8. cfg include/exclude slug filters reach the mount
# ---------------------------------------------------------------------------


async def test_include_exclude_slug_filters(tmp_path: Path) -> None:
    """P5-8 (extra, cfg semantics): include exposes ONLY the named skills;
    exclude omits the named ones (exact-slug semantics); an unknown slug
    fails the run under the 0.73.0 default unknown_names='error' (assembly
    does NOT soften it — a stale cfg slug must be noticed, not dropped)."""
    data = _data_with_fixture_skills(tmp_path)

    include_cfg = _cfg_with_skills(tmp_path, data, include=["scrape-tables"])
    assert await _catalog_names_of_cfg(include_cfg, tmp_path) == ["scrape-tables"]

    exclude_cfg = _cfg_with_skills(tmp_path, data, exclude=["deploy-flask"])
    assert await _catalog_names_of_cfg(exclude_cfg, tmp_path) == ["scrape-tables"]

    exclude_unknown = _cfg_with_skills(tmp_path, data, exclude=["no-such-slug"])
    (cap,) = _mount_capabilities(exclude_unknown)
    workdir = tmp_path / "run-ws-unknown"
    workdir.mkdir()
    agent = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        name="executor",
        capabilities=[cap],
    )
    with pytest.raises(ValueError, match="Unknown skill in exclude: no-such-slug"):
        await agent.run("hi", workspace=LocalWorkspaceBackend(workdir))


# ---------------------------------------------------------------------------
# 9. The deferred catalog never leaks the SKILL.md body
# ---------------------------------------------------------------------------


async def test_mount_never_leaks_skill_body_in_catalog(tmp_path: Path) -> None:
    """P5-9 (extra, D5 invariant): the deferred catalog carries name/
    description/when_to_use ONLY; the body token reaches the wire exclusively
    after an explicit load_capability turn."""
    data = tmp_path / "data"
    (data / "skills" / "token-skill").mkdir(parents=True)
    (data / "skills" / "token-skill" / "SKILL.md").write_text(
        "---\nname: token-skill\ndescription: A skill for body-leak probing.\n"
        "when_to_use: Use when probing leaks.\n---\nUNIQUE-BODY-TOKEN-9173\n",
        encoding="utf-8",
    )
    cfg = _cfg_with_skills(tmp_path, data)
    (cap,) = _mount_capabilities(cfg)
    workdir = tmp_path / "run-ws"
    workdir.mkdir()

    turn_wires: list[str] = []
    state = {"n": 0}

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state["n"] += 1
        wire = _catalog_of(info) + "".join(
            str(getattr(p, "content", "")) for m in messages for p in m.parts
        )
        turn_wires.append(wire)
        if state["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="load_capability",
                        args={"id": "token-skill"},
                        tool_call_id="c1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("ok")])

    agent = Agent(FunctionModel(_model), name="executor", capabilities=[cap])
    await agent.run("hi", workspace=LocalWorkspaceBackend(workdir))
    assert "UNIQUE-BODY-TOKEN-9173" not in turn_wires[0]  # catalog turn: no body
    assert "UNIQUE-BODY-TOKEN-9173" in turn_wires[1]  # after load: body present


# ---------------------------------------------------------------------------
# 10. Disabled toggle -> mount absent
# ---------------------------------------------------------------------------


async def test_disabled_capability_omits_mount(tmp_path: Path) -> None:
    """P5-10 (extra): harness.skills.enabled=false -> no Skills assembled; the
    plain run has neither the catalog nor load_capability."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data, enabled=False)
    assert _mount_capabilities(cfg) == []

    from bob_harness.adapters.executor import ExecutorAdapter

    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    tools: list[list[str]] = []

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools.append(_tool_names(info))
        return ModelResponse(parts=[TextPart("off-ok")])

    adapter = ExecutorAdapter(cfg)
    events = [
        event
        async for event in adapter.run(
            RunSpec(prompt="plain run", workdir=str(workdir), model=FunctionModel(_model))
        )
    ]
    assert [e.kind for e in events] == ["started", "final"]
    assert events[-1].final_text == "off-ok"
    assert tools[0] == []  # no load_capability (skills off), no other capability tools either


# ---------------------------------------------------------------------------
# 11. Workspace requirements (the P5 glue: mount carries its own workspace)
# ---------------------------------------------------------------------------


async def test_mount_requires_workspace_and_executor_binds_one(
    tmp_path: Path,
) -> None:
    """A bare mount WITHOUT workspace= needs the RUN's workspace (UserError
    otherwise, verified 0.73.0); the executor's mount carries its own
    workspace= backend, so a REAL skills-enabled run needs no run workspace
    (test_missing_directories_skip_on_fresh_tree's workdir=None run pins the
    assembled-glue shape; here we pin the raw-capability behavior the glue
    exists to avoid)."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data)
    (cap,) = _mount_capabilities(cfg)
    assert cap.workspace is not None  # D5 glue: the mount always binds one

    # Contrast: a workspace-less mount fails a workspace-less run...
    bare = Skills(directories=[data / "skills"], missing_directories="skip")
    agent = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        name="executor",
        capabilities=[bare],
    )
    with pytest.raises(UserError):
        await agent.run("hi")

    # ...and its catalog resolves fine when a run workspace exists
    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    catalogs: list[str] = []

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        catalogs.append(_catalog_of(info))
        return ModelResponse(parts=[TextPart("ok")])

    agent2 = Agent(FunctionModel(_model), name="executor", capabilities=[bare])
    await agent2.run("hi", workspace=LocalWorkspaceBackend(workdir))
    assert _catalog_names(catalogs[0]) == ["deploy-flask", "scrape-tables"]


# ---------------------------------------------------------------------------
# 12. read_files=True end-to-end: bundled-file read over the assembled mount
# ---------------------------------------------------------------------------


async def test_read_files_true_returns_bundled_bytes(tmp_path: Path) -> None:
    """read_files=True -> a loaded skill's bundled file read returns the
    file's text through the REAL assembled mount (bare agent, FunctionModel
    deferred round-trip)."""
    data = _data_with_fixture_skills(tmp_path)
    cfg = _cfg_with_skills(tmp_path, data, read_files=True)
    caps = _mount_capabilities(cfg)
    assert len(caps) == 1
    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    returns: list[str] = []
    state = {"n": 0}

    def _model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state["n"] += 1
        for m in messages:
            for part in m.parts:
                if isinstance(part, ToolReturnPart):
                    returns.append(str(part.content))
        if state["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="load_capability",
                        args={"id": "scrape-tables"},
                        tool_call_id="c1",
                    )
                ]
            )
        if state["n"] == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read_skill_file",
                        args={"skill": "scrape-tables", "path": "references/deep.md"},
                        tool_call_id="c2",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(_model),
        name="executor",
        capabilities=caps,
    )
    await agent.run("go", workspace=LocalWorkspaceBackend(workdir))
    assert any("deeper material" in text for text in returns), returns
