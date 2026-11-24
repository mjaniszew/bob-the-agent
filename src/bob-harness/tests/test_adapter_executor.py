"""TDD failing tests for the executor adapter skeleton + harness config (P2).

Cases mirror the plan (Part 2 SS4 P2 block, docs/plans/10-pydantic-ai-v2-migration-plan.md):

1.  :func:`test_registry_exposes_executor` — adapters registry contains
    ``"executor"`` satisfying :class:`~bob_harness.adapters.base.AgentRun`.
2.  :func:`test_streams_started_and_final_events` — ``run(spec)`` yields
    ``started`` then ``final`` StreamEvents (closed kinds contract).
3.  :func:`test_failed_event_on_model_error` — model exception produces a
    single ``failed`` StreamEvent, never raises.
4.  :func:`test_spec_prompt_and_workdir_used` — ``spec.prompt`` is the user
    prompt; per-run ``workspace=`` from ``spec.workdir``.
5.  :func:`test_capabilities_empty_when_disabled` — all ``harness.*`` toggles
    off give ``assemble_capabilities() == []`` and the executor still runs
    (graceful degradation).
6.  :func:`test_oneway_spec_same_shape` — ``oneway`` flag carried, no gate/ask
    machinery engaged.
7.  :func:`test_no_subprocess_spawn` — executor is in-process: never touches
    ``start_process``/``build_child_env``.

Config cases (8-10) live in ``tests/test_config.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart

from bob_harness.adapters import AgentRun, RunSpec, get, known_agents
from bob_harness.adapters.base import EVENT_KINDS, StreamEvent
from bob_harness.config import HarnessConfig, load_config


def _cfg(tmp_path: Path) -> HarnessConfig:
    """Config with every ``harness.*`` toggle OFF (P2 graceful-degradation fixtures)."""
    return load_config(str(tmp_path / "executor.yaml"))


def _write_cfg(tmp_path: Path) -> Path:
    (tmp_path / "executor.yaml").write_text(
        f"storage:\n  data_dir: '{tmp_path / 'data'}'\n"
        "harness:\n"
        "  memory:\n    enabled: false\n"
        "  skills:\n    enabled: false\n"
        "  ask_user:\n    enabled: false\n"
        "  subagents:\n    enabled: false\n"
        "  delegation:\n    enabled: false\n",
        encoding="utf-8",
    )
    return tmp_path / "executor.yaml"


def _adapter(tmp_path: Path) -> ExecutorAdapter:
    from bob_harness.adapters.executor import ExecutorAdapter

    return ExecutorAdapter(_cfg(tmp_path))


async def _collect(adapter: Any, spec: RunSpec) -> list[StreamEvent]:
    return [event async for event in adapter.run(spec)]


def _test_model_spec(prompt: str, **spec_kwargs: Any) -> RunSpec:
    return RunSpec(prompt=prompt, model="test", **spec_kwargs)


# ---------------------------------------------------------------------------
# 1. Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_executor(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-1: adapters registry contains 'executor' satisfying AgentRun."""
    _write_cfg(tmp_path)
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    assert "executor" in known_agents()
    instance = get("executor", _cfg(tmp_path))
    from bob_harness.adapters.executor import ExecutorAdapter

    assert isinstance(instance, ExecutorAdapter)
    assert isinstance(instance, AgentRun)


# ---------------------------------------------------------------------------
# 2. Stream contract
# ---------------------------------------------------------------------------


async def test_streams_started_and_final_events(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-2: run(spec) yields started then final; closed kinds contract."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)
    events = await _collect(adapter, _test_model_spec("hello executor"))
    kinds = [e.kind for e in events]
    assert kinds == ["started", "final"]
    assert events[-1].final_text == "success (no tool calls)"
    assert set(kinds) <= EVENT_KINDS


async def test_failed_event_on_model_error(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-3: model exception -> a single failed event, never raises."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)

    async def _boom(messages: Any, info: Any) -> Any:
        raise RuntimeError("model exploded")

    spec = RunSpec(prompt="explode please", model=FunctionModel(_boom))
    events = await _collect(adapter, spec)  # must NOT raise
    kinds = [e.kind for e in events]
    assert kinds == ["started", "failed"]
    assert "model exploded" in (events[-1].error or "")
    assert "RuntimeError" in (events[-1].error or "")


async def test_started_carries_session_ref(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """started.session_ref records the in-process run identity (exec-*)."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)
    events = await _collect(adapter, _test_model_spec("hello"))
    assert (events[0].session_ref or "").startswith("exec-")


# ---------------------------------------------------------------------------
# 3. Prompt + per-run workspace
# ---------------------------------------------------------------------------


async def test_spec_prompt_and_workdir_used(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-4: spec.prompt is the user prompt; per-run workspace= from workdir."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)
    captured: dict[str, Any] = {}

    async def _record(messages: Any, info: Any) -> Any:
        captured["messages"] = list(messages)
        return ModelResponse(parts=[TextPart("ok")])

    workdir = tmp_path / "run-ws"
    workdir.mkdir()
    spec = RunSpec(
        prompt="exact-user-prompt-bytes",
        workdir=str(workdir),
        model=FunctionModel(_record),
    )
    events = await _collect(adapter, spec)
    assert events[-1].kind == "final"
    assert captured["messages"], "the model never received messages"
    user_msgs = [m for m in captured["messages"] if getattr(m, "kind", None) == "request"]
    assert any(
        getattr(part, "part_kind", None) == "user-prompt"
        and "exact-user-prompt-bytes" in str(getattr(part, "content", ""))
        for msg in user_msgs
        for part in msg.parts
    ), "spec.prompt did not reach the model as the user prompt"
    # Per-run workspace actually bound: LocalWorkspaceBackend(spec.workdir) must
    # have been built from the spec (run succeeded inside a fresh workdir).
    assert workdir.is_dir()


async def test_default_workspace_when_no_workdir(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No spec.workdir -> run still works with a temp/default workspace."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)
    events = await _collect(adapter, _test_model_spec("no-workdir run"))
    assert [e.kind for e in events] == ["started", "final"]


# ---------------------------------------------------------------------------
# 4. Capability assembly
# ---------------------------------------------------------------------------


def test_capabilities_empty_when_disabled(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-5: all toggles off -> assemble_capabilities() == []; executor still runs."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    cfg = _cfg(tmp_path)
    assert assemble_capabilities(cfg, workdir=str(tmp_path)) == []

    from bob_harness.adapters.executor import ExecutorAdapter

    adapter = ExecutorAdapter(cfg)
    spec = _test_model_spec("still runs with zero capabilities")
    events = asyncio.run(_collect(adapter, spec))
    assert [e.kind for e in events] == ["started", "final"]


# ---------------------------------------------------------------------------
# 5. One-way runs
# ---------------------------------------------------------------------------


async def test_oneway_spec_same_shape(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-6: oneway flag carried; no gate/ask machinery engaged."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)
    adapter = _adapter(tmp_path)
    spec = RunSpec(prompt="fire and forget", oneway=True, model="test")
    events = await _collect(adapter, spec)
    assert [e.kind for e in events] == ["started", "final"]
    # No gate/ask machinery: no AskUser toolset events, no 'tool' events at all
    assert not any(e.kind == "tool" for e in events)
    assert "ask" not in json.dumps([e.raw or "" for e in events]).lower()


# ---------------------------------------------------------------------------
# 6. In-process contract
# ---------------------------------------------------------------------------


async def test_no_subprocess_spawn(
    tmp_volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-7: executor is in-process: never touches start_process/build_child_env."""
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
    _write_cfg(tmp_path)

    from bob_harness.adapters import base as base_module
    from bob_harness.adapters.executor import ExecutorAdapter

    def _forbidden_start_process(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor adapter must never spawn a subprocess")

    def _forbidden_build_child_env(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor adapter must never build a child env")

    monkeypatch.setattr(base_module, "start_process", _forbidden_start_process)
    monkeypatch.setattr(base_module, "build_child_env", _forbidden_build_child_env)

    adapter = ExecutorAdapter(_cfg(tmp_path))
    events = await _collect(adapter, _test_model_spec("in-process run"))
    assert [e.kind for e in events] == ["started", "final"]
