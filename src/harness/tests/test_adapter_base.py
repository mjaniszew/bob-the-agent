"""Tests for harness.adapters.base (StreamEvent/RunSpec/AgentRun, env allowlist,
process-group helpers, registry)."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

import pytest

from harness.adapters import known_agents, register
from harness.adapters.base import (
    EVENT_KINDS,
    AgentRun,
    RunSpec,
    StreamEvent,
    build_child_env,
    kill_process_group,
    start_process,
)
from harness.adapters.fake import FakeAdapter
from tests.adapters_conftest import FakeProc


def test_stream_event_kinds_are_closed_set() -> None:
    assert EVENT_KINDS == frozenset(
        {"started", "chunk", "final", "tool", "failed", "system"}
    )
    for kind in EVENT_KINDS:
        StreamEvent(kind=kind)
    with pytest.raises(ValueError, match="unknown StreamEvent kind 'bogus'"):
        StreamEvent(kind="bogus")


def test_stream_event_is_frozen_and_defaults() -> None:
    event = StreamEvent(kind="chunk", text="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.text = "no"  # type: ignore[misc]
    assert StreamEvent(kind="failed").text is None


def test_run_spec_defaults() -> None:
    spec = RunSpec(prompt="hello")
    assert spec.prompt == "hello"
    assert spec.profile is None
    assert spec.workdir is None
    assert spec.resume_ref is None
    assert spec.timeout_s is None
    assert spec.oneway is False
    assert spec.files == []
    assert spec.resume_requested is False
    assert spec.model is None
    assert spec.agent is None


def test_agent_run_runtime_checkable() -> None:
    assert isinstance(FakeAdapter({"events": []}), AgentRun)


def test_build_child_env_allowlist_denies_harness_prefix() -> None:
    poisoned = {
        "PATH": "/bin",
        "HOME": "/root",
        "TERM": "xterm",
        "LANG": "C.UTF-8",
        "HARNESS_API_KEY": "secret",
        "HARNESS_ROUTER_BASE_URL": "http://router.example",
        "HARNESS_ROUTER_MODEL": "glm-x",
        "HARNESS_ROUTER_API_KEY": "router-secret",
    }
    env = build_child_env(poisoned, {"HERMES_HOME": "/opt/data"})
    assert env["PATH"] == "/bin"
    assert env["HOME"] == "/root"
    assert env["TERM"] == "xterm"
    assert env["HERMES_HOME"] == "/opt/data"
    for denied in (
        "HARNESS_API_KEY",
        "HARNESS_ROUTER_BASE_URL",
        "HARNESS_ROUTER_MODEL",
        "HARNESS_ROUTER_API_KEY",
        "LANG",
    ):
        assert denied not in env


def test_build_child_env_keep_prefixes_and_passthrough_guard() -> None:
    base_env = {"PATH": "/bin", "HOME": "/x", "OPENCODE_API": "oc", "OTHER": "v"}
    env = build_child_env(base_env, keep_prefixes=("OPENCODE_",))
    assert env == {"PATH": "/bin", "HOME": "/x", "OPENCODE_API": "oc"}
    with pytest.raises(AssertionError, match="HARNESS_"):
        build_child_env({"PATH": "/bin"}, {"HARNESS_BAD": "1"})


async def test_start_process_captured_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List-form spawn with stdin=DEVNULL / PIPEs / start_new_session (kwargs).

    The fake spawner records exactly the kwargs base passes to the (patched)
    asyncio spawner, satisfying the M-2-style "monkeypatched Popen capturing
    kwargs" check; a real-process sibling test asserts the group-leader fact.
    """
    import harness.adapters.base as base_module

    captured: dict = {}

    async def fake_exec(*argv: str, **kwargs: object) -> FakeProc:
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProc([], b"", 0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    proc, created = await base_module.start_process(["/bin/true"], None, {"PATH": "/bin"})
    assert isinstance(proc, FakeProc)
    assert created is True
    assert captured["argv"] == ("/bin/true",)
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["stdout"] == asyncio.subprocess.PIPE
    assert captured["stderr"] == asyncio.subprocess.PIPE
    assert captured["start_new_session"] is True
    assert captured["cwd"] is None
    assert captured["env"] == {"PATH": "/bin"}


async def test_start_process_real_child_is_session_leader() -> None:
    proc, created = await start_process(
        ["/bin/sleep", "30"], None, {"PATH": os.environ.get("PATH", "/bin")}
    )
    assert created is True
    assert proc.stdin is None, "stdin must be DEVNULL (attribute stays None)"
    assert os.getpgid(proc.pid) == proc.pid, "child must be its own group leader"
    await kill_process_group(proc, grace_s=0.5)
    assert proc.returncode == -15


async def test_kill_process_group_escalates_to_sigkill() -> None:
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    proc, _created = await start_process(
        [sys.executable, "-c", code], None, {"PATH": os.environ.get("PATH", "/bin")}
    )
    while True:  # wait until the SIGTERM-ignoring child is actually up
        assert proc.stdout is not None
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        if line.strip():
            break
    started = time.monotonic()
    await kill_process_group(proc, grace_s=0.2)
    assert proc.returncode == -9
    assert time.monotonic() - started < 3, "grace window must not stretch to the full sleep"


async def test_kill_process_group_already_dead_is_noop() -> None:
    proc, _created = await start_process(
        ["/bin/true"], None, {"PATH": os.environ.get("PATH", "/bin")}
    )
    await proc.wait()
    await kill_process_group(proc, grace_s=0.1)
    assert proc.returncode == 0


def test_registry_register_get_known_and_unknown_error() -> None:
    register("test-agent-r4", lambda cfg: cfg)
    try:
        assert "test-agent-r4" in known_agents()
        assert known_agents() == ("fake", "hermes", "opencode", "test-agent-r4")
        marker = object()
        from harness.adapters import get

        assert get("test-agent-r4", marker) is marker
        with pytest.raises(KeyError, match="unknown agent 'nope'"):
            get("nope", None)
    finally:
        import harness.adapters as adapters_module

        adapters_module._REGISTRY.pop("test-agent-r4", None)
