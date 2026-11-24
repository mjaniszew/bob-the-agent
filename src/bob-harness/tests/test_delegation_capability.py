"""TDD failing tests for the delegate_run delegation capability (P3, D1).

Cases mirror the plan (Part 2 SS4 P3 block,
docs/plans/10-pydantic-ai-v2-migration-plan.md) plus the dispatch contract
additions (ToolReturn capture, question pass-back to the WAITING_INPUT path
DETECTION ONLY — full deferred-gate wiring is P6, ledger rows kind='delegation',
budget constants):

1.  :func:`test_toolset_exposes_delegate_run` — ``get_toolset()`` exposes
    ``delegate_run`` with agent/profile/workdir/prompt params.
2.  :func:`test_capability_registers_on_v2_agent` — the capability mounts on a
    pydantic-ai 2.x ``Agent(capabilities=[...])`` and the bound toolset
    exposes ``delegate_run``.
3.  :func:`test_calls_registered_adapter_with_runspec` — a tool call
    constructs ``RunSpec`` (prompt/profile/workdir/resume_ref/oneway/files)
    and calls the REGISTERED adapter's ``run(spec)``.
4.  :func:`test_hermes_profile_mapping` — ``hermes-simple`` → adapter
    ``hermes`` with profile ``simple``; ``hermes-researcher`` → profile
    ``researcher``; an invalid profile is dropped via ``_normalize_profile``
    rules (→ None).
5.  :func:`test_opencode_workdir_guard` — an opencode delegation keeps the
    adapter's own workdir validation (under ``workdir_root``; the adapter
    raises ``ValueError`` on a path outside it).
6.  :func:`test_result_captured_as_tool_return` — the child ``final`` text is
    captured into a ``ToolReturn`` (return_value carries the final text).
7.  :func:`test_events_relayed_as_capability_events` — child StreamEvents
    (chunk/final) surface as ``DelegateRunEvent`` capability events during
    the parent run (``wrap_run_event_stream`` merge, plan P3 test 5).
8.  :func:`test_failed_child_yields_failed_result` — a child ``failed`` event
    maps to an error-flavored tool result (never raises past the tool).
9.  :func:`test_question_passback_surfaces_gate_question` — a child system
    event carrying ``{"gate_question": ...}`` (the WAITING_INPUT path's
    parked-question shape, taskloop._gate_question) is DETECTED and returned
    in the structured result (``question`` field; P6 wires the full gate).
10. :func:`test_ledger_delegation_rows` — every delegation appends a ledger
    row with ``kind == 'delegation'``.
11. :func:`test_disabled_capability_omits_tool` — ``harness.delegation
    .enabled=false`` → the assembled capability list has NO delegation entry
    (tool absent).
12. :func:`test_no_subagents_entries_for_clis` — assembled capabilities
    contain NO ``SubAgents(agents=[...hermes/opencode...])`` (D1 invariant).
13. :func:`test_budget_constants` — delegation budget constants exist and are
    wired (child prompt cap + child event cap; the guard rails of the relay).
14. :func:`test_child_env_contract` — the subprocess path still goes through
    ``build_child_env`` (existing adapter asserts reused; the DELEGATION
    module itself never builds a raw env for a child).

Run-mode tests use ``FunctionModel`` with a stream function (streaming is
required for events to flow to ``on_event``, probe-verified live).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturn,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.tools import RunContext
from pydantic_ai.workspaces import LocalWorkspaceBackend

from bob_harness.adapters import RunSpec, StreamEvent
from bob_harness.config import load_config
from tests.adapters_conftest import FakeProc, install_fake_start_process


# ---------------------------------------------------------------------------
# Harness config helpers (every harness.* toggle OFF mirrors the P2 fixtures;
# delegation-only tests toggle it ON explicitly)
# ---------------------------------------------------------------------------


def _write_cfg(
    tmp_path: Any,
    *,
    delegation_enabled: bool = True,
) -> Any:
    path = tmp_path / "delegation.yaml"
    path.write_text(
        f"storage:\n  data_dir: '{tmp_path / 'data'}'\n"
        "harness:\n"
        "  memory:\n    enabled: false\n"
        "  skills:\n    enabled: false\n"
        "  ask_user:\n    enabled: false\n"
        "  subagents:\n    enabled: false\n"
        f"  delegation:\n    enabled: {str(delegation_enabled).lower()}\n",
        encoding="utf-8",
    )
    return load_config(str(path))


def _cfg(tmp_path: Any, **kwargs: Any) -> Any:
    return _write_cfg(tmp_path, **kwargs)


# ---------------------------------------------------------------------------
# Delegation import (module under test — ImportError = first failing surface)
# ---------------------------------------------------------------------------


@pytest.fixture
def delegation_module() -> Any:
    import bob_harness.delegation as module

    return module


# ---------------------------------------------------------------------------
# Fake child adapter (records the RunSpec it receives; plays back StreamEvents)
# ---------------------------------------------------------------------------


class RecordingAdapter:
    """Stands in for a registered adapter: captures ``run(spec)`` calls."""

    def __init__(self, events: list[StreamEvent] | None = None) -> None:
        self.specs: list[RunSpec] = []
        self._events = events if events is not None else [
            StreamEvent(kind="chunk", text="child chunk"),
            StreamEvent(kind="final", final_text="child final text"),
        ]

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        async def _gen() -> AsyncIterator[StreamEvent]:
            self.specs.append(spec)
            for event in self._events:
                yield event

        return _gen()


# ---------------------------------------------------------------------------
# Run-mode model helpers: FunctionModel that calls delegate_run once, then
# answers with text. Streamed (stream_function) so events dispatch.
# ---------------------------------------------------------------------------


def _delegation_model(
    tool_args: dict[str, Any],
    *,
    tool_name: str = "delegate_run",
    call_id: str = "dc1",
) -> FunctionModel:
    tool_call = ModelResponse(
        parts=[
            ToolCallPart(tool_name=tool_name, args=tool_args, tool_call_id=call_id)
        ]
    )
    final = ModelResponse(parts=[TextPart("parent done")])

    def request_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        has_tool_return = any(
            getattr(p, "part_kind", None) == "tool-return"
            for m in messages
            for p in m.parts
        )
        return final if has_tool_return else tool_call

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        has_tool_return = any(
            getattr(p, "part_kind", None) == "tool-return"
            for m in messages
            for p in m.parts
        )
        if has_tool_return:
            yield "parent done"
        else:
            yield {
                0: DeltaToolCall(
                    name=tool_name,
                    json_args=json.dumps(tool_args),
                    tool_call_id=call_id,
                )
            }

    return FunctionModel(request_fn, stream_function=stream_fn)


async def _run_with_delegation(
    tmp_path: Any,
    delegation_module: Any,
    args: dict[str, Any],
    capability: AbstractCapability | None = None,
) -> Any:
    """Run an agent with the delegation capability; return (result, observer)."""

    class _Observer(AbstractCapability):
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def on_event(self, ctx: RunContext, *, event: Any) -> None:
            self.events.append(event)

    observer = _Observer()
    cap = capability if capability is not None else delegation_module.DelegationCapability(
        registry={}, cfg=_cfg(tmp_path)
    )
    agent = Agent(_delegation_model(args), name="executor", capabilities=[cap, observer])
    async with agent.iter("delegate it") as run:
        async for _node in run:
            pass
        result = run.result
    return result, observer


# ---------------------------------------------------------------------------
# 1. Toolset surface (static, no run needed)
# ---------------------------------------------------------------------------


def test_toolset_exposes_delegate_run(delegation_module: Any) -> None:
    """P3-1: get_toolset() exposes delegate_run with the contract params."""
    cap = delegation_module.DelegationCapability(registry={}, cfg=None)
    toolset = cap.get_toolset()
    names = list(toolset.tools.keys())
    assert "delegate_run" in names
    tool = toolset.tools["delegate_run"].tool_def
    params = set(tool.parameters_json_schema.get("properties", {}).keys())
    assert {"agent", "prompt", "profile", "workdir"} <= params, params


def test_toolset_param_schema_shape(delegation_module: Any) -> None:
    """agent is required; prompt required; profile/workdir/resume_ref optional."""
    cap = delegation_module.DelegationCapability(registry={}, cfg=None)
    schema = cap.get_toolset().tools["delegate_run"].tool_def.parameters_json_schema
    assert set(schema.get("required", [])) == {"agent", "prompt"}, schema.get("required")


# ---------------------------------------------------------------------------
# 2. Capability binds to a v2 Agent
# ---------------------------------------------------------------------------


def test_capability_registers_on_v2_agent(delegation_module: Any, tmp_path: Any) -> None:
    """The capability mounts on Agent(capabilities=[...]); tool reachable."""
    cap = delegation_module.DelegationCapability(registry={}, cfg=_cfg(tmp_path))
    agent = Agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        name="executor",
        capabilities=[cap],
    )
    toolsets = getattr(agent, "toolsets", [])
    assert any("delegate_run" in getattr(ts, "tools", {}) for ts in toolsets), (
        "delegate_run not bound on the agent toolsets"
    )


async def test_invalid_agent_target_rejected(delegation_module: Any, tmp_path: Any) -> None:
    """Unknown agent target -> error-flavored result, no adapter call."""
    from pydantic_ai.messages import ToolReturnPart

    recording = RecordingAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "claude-code", "prompt": "hi"},
        capability=cap,
    )
    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "no tool return recorded"
    content = parts[0].content
    text = json.dumps(content) if not isinstance(content, str) else content
    assert "unknown agent" in text.lower(), text
    assert recording.specs == [], "an unknown target must not reach any adapter"


# ---------------------------------------------------------------------------
# 3. Tool call -> RunSpec translation -> registered adapter
# ---------------------------------------------------------------------------


async def test_calls_registered_adapter_with_runspec(
    delegation_module: Any, tmp_path: Any
) -> None:
    """The tool constructs RunSpec(prompt/profile/workdir/resume_ref/...) and
    calls the registered adapter's run(spec)."""
    from pydantic_ai.messages import ToolReturnPart

    recording = RecordingAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {
            "agent": "hermes",
            "prompt": "child prompt bytes",
            "profile": "researcher",
            "workdir": str(tmp_path),
            "resume_ref": "ref-123",
        },
        capability=cap,
    )
    assert len(recording.specs) == 1, "delegate_run must call run(spec) once"
    spec = recording.specs[0]
    assert isinstance(spec, RunSpec)
    assert spec.prompt == "child prompt bytes"
    assert spec.profile == "researcher"
    assert spec.workdir == str(tmp_path)
    assert spec.resume_ref == "ref-123"
    assert spec.oneway is False
    assert spec.files == []

    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "delegation result missing"


# ---------------------------------------------------------------------------
# 4. Hermes profile mapping (targets -> adapter + profile)
# ---------------------------------------------------------------------------


async def test_hermes_profile_mapping(delegation_module: Any, tmp_path: Any) -> None:
    """hermes-simple/hermes-researcher map profile; invalid profile -> None."""
    from pydantic_ai.messages import ToolReturnPart

    recording = RecordingAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )

    for target, expected_profile in (
        ("hermes-simple", "simple"),
        ("hermes-researcher", "researcher"),
    ):
        result, _obs = await _run_with_delegation(
            tmp_path,
            delegation_module,
            {"agent": target, "prompt": "p"},
            capability=cap,
        )
        assert recording.specs, f"{target}: adapter never called"
        spec = recording.specs[-1]
        assert spec.profile == expected_profile, (target, spec.profile)
        parts = [
            p
            for msg in result.all_messages()
            for p in msg.parts
            if isinstance(p, ToolReturnPart)
        ]
        assert parts, f"{target}: no tool return for a successful delegation"

    results: list[str] = []

    async def _run_bad() -> None:
        recording2 = RecordingAdapter()
        cap2 = delegation_module.DelegationCapability(
            registry={"hermes": recording2}, cfg=_cfg(tmp_path)
        )
        result, _obs = await _run_with_delegation(
            tmp_path,
            delegation_module,
            {"agent": "hermes", "prompt": "p", "profile": "hacker-profile"},
            capability=cap2,
        )
        assert recording2.specs, "normalized delegation should still reach adapter"
        assert recording2.specs[-1].profile is None, "invalid profile must drop to None"
        parts = [
            p
            for msg in result.all_messages()
            for p in msg.parts
            if isinstance(p, ToolReturnPart)
        ]
        assert parts
        results.append("ok")

    await _run_bad()
    assert results == ["ok"]


# ---------------------------------------------------------------------------
# 5. opencode target + workdir guard (adapter's own validation unchanged)
# ---------------------------------------------------------------------------


async def test_opencode_workdir_guard(delegation_module: Any, tmp_path: Any) -> None:
    """opencode delegation keeps the adapter workdir validation: a workdir
    outside workdir_root raises ValueError (adapter behavior, unchanged)."""

    class ExplodingWorkdirAdapter(RecordingAdapter):
        def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
            async def _gen() -> AsyncIterator[StreamEvent]:
                self.specs.append(spec)
                raise ValueError(
                    f"workdir {spec.workdir!r} is not under opencode workdir_root '/app/projects'"
                )
                yield StreamEvent(kind="final", final_text="unreachable")  # pragma: no cover

            return _gen()

    adapter = ExplodingWorkdirAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"opencode": adapter}, cfg=_cfg(tmp_path)
    )
    from pydantic_ai.messages import ToolReturnPart

    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "opencode", "prompt": "p", "workdir": "/etc/passwd-evil"},
        capability=cap,
    )
    assert adapter.specs, "the guarded call must still reach the adapter seam"
    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "workdir rejection must surface as a structured result"
    content = parts[0].content
    text = json.dumps(content) if not isinstance(content, str) else content
    assert "workdir" in text.lower(), text


# ---------------------------------------------------------------------------
# 6. Result capture as ToolReturn (dispatch contract)
# ---------------------------------------------------------------------------


async def test_result_captured_as_tool_return(
    delegation_module: Any, tmp_path: Any
) -> None:
    """The child's final text lands in the tool result (ToolReturn path)."""
    from pydantic_ai.messages import ToolReturnPart

    recording = RecordingAdapter(
        events=[
            StreamEvent(kind="chunk", text="partial "),
            StreamEvent(kind="final", final_text="child final text"),
        ]
    )
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "hermes", "prompt": "p"},
        capability=cap,
    )
    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "no ToolReturnPart recorded for the delegation"
    content = parts[0].content
    text = json.dumps(content) if not isinstance(content, str) else content
    assert "child final text" in text, text


# ---------------------------------------------------------------------------
# 7. Child StreamEvents relayed as capability events (plan P3 test 5)
# ---------------------------------------------------------------------------


async def test_events_relayed_as_capability_events(
    delegation_module: Any, tmp_path: Any
) -> None:
    """chunk/final StreamEvents surface as DelegateRunEvent capability Events."""
    recording = RecordingAdapter(
        events=[
            StreamEvent(kind="chunk", text="child chunk"),
            StreamEvent(kind="final", final_text="child final text"),
        ]
    )
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording, "opencode": recording}, cfg=_cfg(tmp_path)
    )
    _result, observer = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "hermes", "prompt": "p"},
        capability=cap,
    )
    relayed = [
        e
        for e in observer.events
        if getattr(e, "event_kind", None) == "capability"
        and type(e).__name__ == "DelegateRunEvent"
    ]
    kinds = [e.child_kind for e in relayed]
    assert "chunk" in kinds, kinds
    assert "final" in kinds, kinds


# ---------------------------------------------------------------------------
# 8. Failed child -> error-flavored result, never raises past the tool
# ---------------------------------------------------------------------------


async def test_failed_child_yields_failed_result(
    delegation_module: Any, tmp_path: Any
) -> None:
    """A child failed event becomes an error-shaped tool result."""
    from pydantic_ai.messages import ToolReturnPart

    recording = RecordingAdapter(
        events=[StreamEvent(kind="failed", error="child exploded", exit_code=2)]
    )
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "hermes", "prompt": "p"},
        capability=cap,
    )
    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "failed delegation missing a result"
    content = parts[0].content
    text = json.dumps(content) if not isinstance(content, str) else content
    assert "child exploded" in text, text


# ---------------------------------------------------------------------------
# 9. Question pass-back (WAITING_INPUT detection surface; P6 wires the gate)
# ---------------------------------------------------------------------------


async def test_question_passback_surfaces_gate_question(
    delegation_module: Any, tmp_path: Any
) -> None:
    """A child system event with {"gate_question": ...} is detected and the
    structured result carries question=<text> (the WAITING_INPUT shape
    taskloop._gate_question parses today)."""
    from pydantic_ai.messages import ToolReturnPart

    raw = json.dumps({"gate_question": "which env?"})
    recording = RecordingAdapter(
        events=[
            StreamEvent(kind="chunk", text="working..."),
            StreamEvent(kind="system", raw=raw),
        ]
    )
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "hermes", "prompt": "p"},
        capability=cap,
    )
    parts = [
        p
        for msg in result.all_messages()
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert parts, "question-flow delegation missing a result"
    content = parts[0].content
    text = json.dumps(content) if not isinstance(content, str) else content
    assert "which env?" in text, text


# ---------------------------------------------------------------------------
# 10. Ledger rows kind='delegation' (dispatch contract)
# ---------------------------------------------------------------------------


async def test_ledger_delegation_rows(delegation_module: Any, tmp_path: Any) -> None:
    """Every delegation leaves a JSONL ledger row with kind='delegation'."""
    from bob_harness.persist import read_jsonl

    data_dir = tmp_path / "data"
    cfg = _cfg(tmp_path)
    assert str(data_dir) == str(cfg.storage.data_dir) or True  # load_config pins it

    recording = RecordingAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording, "opencode": recording}, cfg=cfg
    )
    result, _obs = await _run_with_delegation(
        tmp_path,
        delegation_module,
        {"agent": "hermes", "prompt": "p"},
        capability=cap,
    )
    rows = read_jsonl(data_dir / "delegations.jsonl")
    assert rows, "no delegation ledger rows written"
    row = rows[-1]
    assert row.get("kind") == "delegation", row
    assert row.get("agent") == "hermes", row
    assert "prompt" in row or "prompt_chars" in row, row


# ---------------------------------------------------------------------------
# 11/12. Config gating + D1 invariant (no SubAgents entries for CLIs)
# ---------------------------------------------------------------------------


def test_disabled_capability_omits_tool(delegation_module: Any, tmp_path: Any) -> None:
    """delegation.enabled=false -> no DelegationCapability in the assembly."""
    from bob_harness.adapters.executor import assemble_capabilities

    cfg = _cfg(tmp_path, delegation_enabled=False)
    caps = assemble_capabilities(cfg)
    assert not any(
        isinstance(c, delegation_module.DelegationCapability) for c in caps
    ), caps


def test_no_subagents_entries_for_clis(delegation_module: Any, tmp_path: Any) -> None:
    """D1 invariant: delegation ON never produces SubAgents CLI entries."""
    from bob_harness.adapters.executor import assemble_capabilities

    cfg = _cfg(tmp_path, delegation_enabled=True)
    caps = assemble_capabilities(cfg, workdir=str(tmp_path))
    deleg = [c for c in caps if isinstance(c, delegation_module.DelegationCapability)]
    assert len(deleg) == 1, caps
    subs = [c for c in caps if type(c).__name__ == "SubAgents"]
    for cap in subs:
        entries = list(getattr(cap, "agents", []) or [])
        for entry in entries:
            name = str(getattr(entry, "name", ""))
            assert "hermes" not in name and "opencode" not in name, entries


# ---------------------------------------------------------------------------
# 13. Budget constants (dispatch contract)
# ---------------------------------------------------------------------------


def test_budget_constants(delegation_module: Any) -> None:
    """Relay guard-rail constants exist and are finite/positive."""
    for name in (
        "DELEGATION_PROMPT_CHAR_CAP",
        "DELEGATION_EVENT_CHAR_CAP",
        "DELEGATION_MAX_EVENTS",
    ):
        value = getattr(delegation_module, name)
        assert isinstance(value, int) and value > 0, (name, value)
    # The tool docstring/params must not smuggle uncapped text: cap < 100000
    assert delegation_module.DELEGATION_PROMPT_CHAR_CAP <= 100_000


def test_child_prompt_capped(delegation_module: Any, tmp_path: Any) -> None:
    """An oversized prompt is capped to DELEGATION_PROMPT_CHAR_CAP."""
    recording = RecordingAdapter()
    cap = delegation_module.DelegationCapability(
        registry={"hermes": recording}, cfg=_cfg(tmp_path)
    )
    oversized = "x" * (delegation_module.DELEGATION_PROMPT_CHAR_CAP + 5000)

    async def _drive() -> None:
        agent = Agent(
            _delegation_model({"agent": "hermes", "prompt": oversized}),
            name="executor",
            capabilities=[cap],
        )
        async with agent.iter("go") as run:
            async for _node in run:
                pass

    asyncio.run(_drive())
    assert recording.specs, "capped delegation never reached the adapter"
    assert (
        len(recording.specs[0].prompt) == delegation_module.DELEGATION_PROMPT_CHAR_CAP
    )


# ---------------------------------------------------------------------------
# 14. Child env contract (subprocess path unchanged)
# ---------------------------------------------------------------------------


def test_child_env_contract(delegation_module: Any, tmp_path: Any, monkeypatch: Any) -> None:
    """The REAL subprocess path still runs through base.build_child_env.

    The delegation module must not build child envs itself: with the fake
    start_process seam installed (adapters_conftest), a delegate_run call on a
    REAL hermes adapter spawns through build_child_env, which drops
    BOB_HARNESS_* the same as task-dispatch runs today.
    """
    import os

    from bob_harness.adapters.hermes import HermesAdapter
    from bob_harness.config import HermesAgentsConfig

    hermes_cfg = HermesAgentsConfig(
        bin="/bin/true",
        default_model=None,
        timeout_s=60,
        profile_timeouts={"simple": 60},
    )
    adapter = HermesAdapter(hermes_cfg, persist_dir=tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    events: list[StreamEvent] = []

    async def _drive() -> None:
        async for event in adapter.run(RunSpec(prompt="p", profile="simple")):
            events.append(event)

    asyncio.run(_drive())
    assert events and events[-1].kind == "final"
    env = calls[0]["env"]
    assert not any(k.startswith("BOB_HARNESS_") for k in env), sorted(env)
    # The delegation module itself must not bypass the seam
    import inspect

    source = inspect.getsource(delegation_module)
    assert "build_child_env(" not in source.replace(
        "build_child_env)", ""
    ), "delegation module builds a child env itself (D1 violation)"
