"""Tests for harness.adapters.fake (scripted scenarios, assertions mode,
registry integration)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness.adapters import get
from harness.adapters.base import AgentRun, RunSpec
from harness.adapters.fake import FakeAdapter

_CHUNK_TOOL_FINAL = {
    "events": [
        {"kind": "chunk", "text": "hello"},
        {"kind": "tool", "tool_name": "bash", "tool_brief": "echo hi"},
        {"kind": "final", "final_text": "hello world"},
    ]
}


async def test_scripted_yaml_scenario_streams_in_order(tmp_path: Path) -> None:
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(
        "events:\n"
        "  - kind: chunk\n"
        "    text: hello\n"
        "  - kind: tool\n"
        "    tool_name: bash\n"
        "    tool_brief: echo hi\n"
        "  - kind: final\n"
        "    final_text: hello world\n"
        "exit_code: 0\n"
        "failed_after: null\n",
        encoding="utf-8",
    )
    adapter = FakeAdapter(scenario_file)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["chunk", "tool", "final"]
    assert events[1].tool_name == "bash" and events[1].tool_brief == "echo hi"
    assert events[2].final_text == "hello world"


async def test_scripted_json_scenario_streams_in_order(tmp_path: Path) -> None:
    """JSON files parse too (yaml.safe_load accepts JSON)."""
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(_CHUNK_TOOL_FINAL), encoding="utf-8")
    adapter = FakeAdapter(scenario_file)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["chunk", "tool", "final"]
    assert events[0].text == "hello"
    assert events[1].tool_name == "bash"
    assert events[2].final_text == "hello world"


async def test_scripted_inline_dict_without_final_synth_one() -> None:
    no_final = {
        "events": [
            {"kind": "chunk", "text": "a"},
            {"kind": "chunk", "text": "b"},
        ]
    }
    adapter = FakeAdapter(no_final)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["chunk", "chunk", "final"]
    assert events[-1].final_text == "ab"


async def test_failed_after_sleeps_then_emits_timeout_simulation() -> None:
    scenario = {
        "events": [{"kind": "chunk", "text": "partial"}],
        "failed_after": 0.05,
    }
    adapter = FakeAdapter(scenario)
    started = time.monotonic()
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert time.monotonic() - started >= 0.05
    assert [event.kind for event in events] == ["chunk", "failed"]
    assert events[-1].error == "timeout simulation"


async def test_exit_code_nonzero_makes_final_event_failed() -> None:
    scenario = {
        "events": [{"kind": "chunk", "text": "x"}],
        "exit_code": 5,
    }
    adapter = FakeAdapter(scenario)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["chunk", "failed"]
    assert events[-1].exit_code == 5
    assert events[-1].error == "exit code 5"


async def test_argv_when_run_surfaces_in_first_system_event() -> None:
    """Assertions mode: argv_when_run streams out as the FIRST system event."""
    scenario = {
        "events": [{"kind": "chunk", "text": "hi"}],
        "argv_when_run": ["hermes", "-p", "simple"],
    }
    adapter = FakeAdapter(scenario)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert events[0].kind == "system"
    assert json.loads(events[0].raw or "") == ["hermes", "-p", "simple"]
    assert [event.kind for event in events] == ["system", "chunk", "final"]


async def test_delay_s_slow_stream_is_respected() -> None:
    scenario = {
        "events": [
            {"kind": "chunk", "text": "one"},
            {"kind": "chunk", "text": "two"},
        ],
        "delay_s": 0.01,
    }
    adapter = FakeAdapter(scenario)
    started = time.monotonic()
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert time.monotonic() - started >= 0.015
    assert [(event.kind, event.text) for event in events[:2]] == [
        ("chunk", "one"),
        ("chunk", "two"),
    ]
    assert events[-1].kind == "final"


def test_bogus_event_kind_rejects_scenario() -> None:
    with pytest.raises(ValueError, match="unknown StreamEvent kind"):
        FakeAdapter({"events": [{"kind": "bogus"}]})


def test_non_mapping_scenario_is_rejected(tmp_path: Path) -> None:
    """Files whose YAML content is not a mapping and inline non-mapping values
    are both rejected with a readable ValueError."""
    scalar_file = tmp_path / "list.yaml"
    scalar_file.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(TypeError, match="must be a mapping"):
        FakeAdapter(scalar_file)
    with pytest.raises(TypeError, match="must be a mapping"):
        FakeAdapter(["not", "a", "mapping"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a mapping"):
        FakeAdapter(42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="with a 'kind'"):
        FakeAdapter({"events": [{"text": "no kind"}]})


async def test_registry_integration_streams_scripted_sequence() -> None:
    """get('fake', scenario) wires the registry to FakeAdapter streams."""
    adapter = get("fake", _CHUNK_TOOL_FINAL)
    assert isinstance(adapter, FakeAdapter)
    assert isinstance(adapter, AgentRun)
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["chunk", "tool", "final"]
    assert events[0].text == "hello"


async def test_registry_fake_scenario_accepts_files(tmp_path: Path) -> None:
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text("events:\n  - kind: started\n", encoding="utf-8")
    adapter = get("fake", str(scenario_file))
    events = [event async for event in adapter.run(RunSpec(prompt="p"))]
    assert [event.kind for event in events] == ["started", "final"]
    assert events[-1].final_text == ""
