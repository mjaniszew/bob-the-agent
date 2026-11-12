"""Tests for harness.adapters.opencode (argv snapshots, workdir validation,
F11 pre-seed, NDJSON parse, failures, watchdog, ledger)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from harness.adapters import get
from harness.adapters.base import RunSpec, build_child_env
from harness.adapters.opencode import (
    OpencodeAdapter,
    extract_session_id,
    preseed_opencode_json,
)
from harness.config import OpencodeAgentsConfig, load_config
from harness.persist import read_jsonl
from tests.adapters_conftest import (
    FakeProc,
    fixture_lines,
    install_fake_start_process,
)

_BIN = "opencode"
_SEED = {
    "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
    "external_directory": "allow",
}


def _sub_config(tmp_path: Path, **overrides: object) -> OpencodeAgentsConfig:
    values: dict[str, object] = {
        "bin": _BIN,
        "default_model": None,
        "default_agent": "build",
        "timeout_s": 7200,
        "workdir_root": str(tmp_path / "projects"),
        "auto_approve": True,
    }
    values.update(overrides)
    return OpencodeAgentsConfig(**values)  # type: ignore[arg-type]


def _adapter(tmp_path: Path, **overrides: object) -> OpencodeAdapter:
    return OpencodeAdapter(_sub_config(tmp_path, **overrides), persist_dir=tmp_path)


def _workdir(tmp_path: Path, name: str = "demo") -> str:
    return str(tmp_path / "projects" / name)


# --- argv snapshot matrix ---------------------------------------------------


async def test_argv_snapshot_fresh_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh run: bin, run, <prompt-as-one-argv-element>, --format json,
    --agent (always), --dir (m3), --print-logs, --log-level WARN, --auto."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="echo hi", workdir=workdir))]
    argv = calls[0]["argv"]
    assert argv == [
        _BIN,
        "run",
        "echo hi",
        "--format",
        "json",
        "--agent",
        "build",
        "--dir",
        workdir,
        "--print-logs",
        "--log-level",
        "WARN",
        "--auto",
    ]
    assert calls[0]["cwd"] == workdir, "m3: cwd AND --dir both set to the workdir"
    assert json.dumps(argv).count("echo hi") == 1


async def test_argv_snapshot_session_resume_continue_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume_ref -> --session <id>; resume_requested (no ref) -> --continue;
    neither -> no resume flag pair at all."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "resume")
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [
        event
        async for event in adapter.run(
            RunSpec(prompt="go", workdir=workdir, resume_ref="ses_abc123")
        )
    ]
    assert calls[0]["argv"][calls[0]["argv"].index("--session") + 1] == "ses_abc123"
    assert "--continue" not in calls[0]["argv"]

    _ = [
        event
        async for event in adapter.run(
            RunSpec(prompt="go", workdir=workdir, resume_requested=True)
        )
    ]
    assert "--continue" in calls[1]["argv"]
    assert "--session" not in calls[1]["argv"]

    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert "--continue" not in calls[2]["argv"]
    assert "--session" not in calls[2]["argv"]


async def test_argv_snapshot_model_and_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--model from spec.model else config default (omitted when neither);
    --agent always present, spec.agent overrides the config default."""
    adapter = _adapter(tmp_path, default_model="cfg-model")
    workdir = _workdir(tmp_path, "ma")
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [
        event
        async for event in adapter.run(
            RunSpec(prompt="go", workdir=workdir, model="spec-model", agent="plan")
        )
    ]
    argv = calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "spec-model"
    assert argv[argv.index("--agent") + 1] == "plan"
    assert argv.index("--model") < argv.index("--agent") < argv.index("--dir")

    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    argv2 = calls[1]["argv"]
    assert argv2[argv2.index("--model") + 1] == "cfg-model"
    assert argv2[argv2.index("--agent") + 1] == "build"


async def test_argv_snapshot_no_auto_when_approval_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path, auto_approve=False)
    workdir = _workdir(tmp_path, "noauto")
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert "--auto" not in calls[0]["argv"]


# --- workdir validation (m3) -----------------------------------------------


def test_workdir_must_live_under_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-root adapter (^/app/projects/): /tmp/x rejected; nested ok;
    a custom workdir_root adapter builds its regex dynamically."""
    monkeypatch.setenv("HARNESS_API_KEY", "test-key")
    monkeypatch.delenv("HARNESS_WORKDIR", raising=False)
    default_adapter = OpencodeAdapter(load_config())
    with pytest.raises(ValueError, match="not under opencode workdir_root"):
        default_adapter._validate_workdir(RunSpec(prompt="x", workdir="/tmp/x"))
    with pytest.raises(ValueError, match="requires a workdir"):
        default_adapter._validate_workdir(RunSpec(prompt="x"))
    assert (
        default_adapter._validate_workdir(RunSpec(prompt="x", workdir="/app/projects/a/b"))
        == "/app/projects/a/b"
    )

    adapter = _adapter(tmp_path)
    assert (
        adapter._validate_workdir(RunSpec(prompt="x", workdir=_workdir(tmp_path)))
        == _workdir(tmp_path)
    )
    with pytest.raises(ValueError, match="not under opencode workdir_root"):
        adapter._validate_workdir(RunSpec(prompt="x", workdir="/tmp/elsewhere"))


# --- opencode.json pre-seed (F11) ------------------------------------------


async def test_preseed_creates_opencode_json_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "seed")
    install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    written = json.loads((Path(workdir) / "opencode.json").read_text(encoding="utf-8"))
    assert written == _SEED
    assert preseed_opencode_json(Path(workdir)) is False
    assert (Path(workdir) / "opencode.json").is_file()
    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]


async def test_preseed_never_overwrites_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    workdir_str = _workdir(tmp_path, "keep")
    workdir = Path(workdir_str)
    workdir.mkdir(parents=True)
    existing = workdir / "opencode.json"
    existing.write_text('{"permission": {"bash": "deny"}}\n', encoding="utf-8")
    before = existing.read_bytes()
    install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir_str))]
    assert existing.read_bytes() == before


# --- NDJSON parse (recorded fixture) ---------------------------------------


def _event_stream(lines: list[bytes], exit_code: int = 0, **kwargs: object) -> FakeProc:
    stderr_chunks = kwargs.pop("stderr", b"")  # type: ignore[return-value]
    return FakeProc(lines, stderr_chunks, exit_code=exit_code, **kwargs)  # type: ignore[arg-type]


async def test_ndjson_parse_recorded_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opencode-events.jsonl: step noise skipped, tool surfaced, text chunked,
    final = joined text parts with the fixture sessionID."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "events")
    install_fake_start_process(
        monkeypatch, _event_stream(fixture_lines("opencode-events.jsonl"))
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert [event.kind for event in events] == ["tool", "chunk", "final"]
    assert events[0].tool_name == "bash"
    assert events[0].tool_brief == "echo THIRD_MARKER"
    assert events[1].text == "THIRD_MARKER"
    assert events[2].final_text == "THIRD_MARKER"
    assert events[2].session_ref == "ses_e2e14039effe1cwxrU6lI5Tb4W"


async def test_ndjson_parse_live_probe_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opencode-live-probe.out: fresh run -> final PROBE_OK + sessionID."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "probe")
    install_fake_start_process(
        monkeypatch, _event_stream(fixture_lines("opencode-live-probe.out"))
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert [event.kind for event in events] == ["chunk", "final"]
    assert events[0].text == "PROBE_OK"
    assert events[1].final_text == "PROBE_OK"
    assert events[1].session_ref == "ses_e2e152e36ffe9Mn2MUCCEG6vuA"


async def test_f3_garbage_line_relayed_as_system_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage stdout line -> system(raw) event, stream continues (F3)."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "garbage")
    install_fake_start_process(
        monkeypatch,
        _event_stream(
            [
                b"this is not json at all\n",
                *fixture_lines("opencode-live-probe.out"),
            ]
        ),
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    kinds = [event.kind for event in events]
    assert kinds == ["system", "chunk", "final"]
    assert events[0].raw == "this is not json at all"


async def test_f3_unknown_type_relayed_as_system_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "unknown")
    install_fake_start_process(
        monkeypatch, _event_stream([b'{"type":"mystery","sessionID":"ses_xyz"}\n'])
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    kinds = [event.kind for event in events]
    assert kinds == ["system", "final"]
    assert events[0].raw == '{"type":"mystery","sessionID":"ses_xyz"}'
    assert events[1].session_ref == "ses_xyz"


async def test_tool_brief_falls_back_to_input_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "brief")
    install_fake_start_process(
        monkeypatch,
        _event_stream(
            [
                json.dumps(
                    {
                        "type": "tool_use",
                        "sessionID": "s1",
                        "part": {
                            "type": "tool",
                            "tool": "bash",
                            "state": {"input": {"cmd": "x" * 200}},
                        },
                    }
                ).encode() + b"\n"
            ]
        ),
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    tool = events[0]
    assert tool.kind == "tool" and tool.tool_name == "bash"
    assert tool.tool_brief is not None and len(tool.tool_brief) <= 80


# --- failures, watchdog ----------------------------------------------------


async def test_error_event_maps_to_failed_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure probe error line: failed(error text), no final, ledger error."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "err")
    calls = install_fake_start_process(
        monkeypatch, _event_stream(fixture_lines("opencode-failure-probe.out"))
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert [event.kind for event in events] == ["failed"]
    assert (
        events[0].error == "Unexpected server error. Check server logs for details."
    )
    assert events[0].exit_code is None
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row["agent"] == "opencode"
    assert row["argv_redacted"] == calls[0]["argv"]
    assert row["error"] == events[0].error
    assert "duration_s" in row and "ts" in row


async def test_f1_nonzero_exit_maps_to_failed_with_stderr_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nonzero exit WITHOUT an error event: final still emitted, then failed."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "exit1")
    stderr = b"X" * 300 + b"Model not found: bogus." + b"Z" * 300
    install_fake_start_process(
        monkeypatch,
        _event_stream(
            fixture_lines("opencode-live-probe.out"),
            stderr=stderr,
            exit_code=1,
        ),
    )
    events = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    assert [event.kind for event in events] == ["chunk", "final", "failed"]
    failed = events[-1]
    assert failed.exit_code == 1
    assert len(failed.error or "") > 500  # includes the prefix; tail alone <= 500
    assert failed.error == f"opencode exited with code 1: {stderr.decode()[-500:]}"
    assert "Model not found: bogus." in (failed.error or "")


async def test_f2_watchdog_timeout_kills_sleeping_child(
    tmp_path: Path,
) -> None:
    """Real /bin/sleep 30 in a tmp workdir; 1s budget -> failed 'timeout after 1s'."""
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "timeout")
    adapter._build_argv = lambda spec, wd: ["/bin/sleep", "30"]  # type: ignore[method-assign]
    started = time.monotonic()
    events = [
        event
        async for event in adapter.run(
            RunSpec(prompt="ignored", workdir=workdir, timeout_s=1)
        )
    ]
    assert time.monotonic() - started < 5
    assert [event.kind for event in events] == ["failed"]
    assert events[-1].error == "timeout after 1s"
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row["error"] == "timeout after 1s"
    assert row["exit_code"] == -15


async def test_f12_missing_bin_maps_to_failed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, bin="/opt/definitely-not-here/opencode")
    workdir = _workdir(tmp_path, "missingbin")
    events = [
        event
        async for event in adapter.run(
            RunSpec(prompt="go", workdir=workdir, timeout_s=5)
        )
    ]
    failed = events[-1]
    assert events == [failed]
    assert failed.kind == "failed"
    assert failed.exit_code is None
    assert "failed to spawn opencode" in (failed.error or "")
    assert "No such file" in (failed.error or "")


# --- env allowlist (M-3, opencode flavor) -----------------------------------


def test_build_child_env_keeps_opencode_prefix_and_denies_harness() -> None:
    base_env = {
        "PATH": "/bin",
        "HOME": "/x",
        "TERM": "xterm",
        "OPENCODE_CONFIG": "/oc/config.json",
        "OPENCODE_API": "oc-secret",
        "HARNESS_API_KEY": "secret",
        "HARNESS_ROUTER_MODEL": "glm-x",
    }
    env = build_child_env(base_env, keep_prefixes=("OPENCODE_",))
    assert env["OPENCODE_CONFIG"] == "/oc/config.json"
    assert env["OPENCODE_API"] == "oc-secret"
    for kept in ("PATH", "HOME", "TERM"):
        assert env[kept] == base_env[kept]
    for denied in ("HARNESS_API_KEY", "HARNESS_ROUTER_MODEL"):
        assert denied not in env


async def test_spawn_env_forwards_opencode_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    workdir = _workdir(tmp_path, "envfwd")
    monkeypatch.setenv("OPENCODE_CONFIG", "/oc/config.json")
    monkeypatch.setenv("HARNESS_API_KEY", "topsecret")
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="go", workdir=workdir))]
    env = calls[0]["env"]
    assert env["OPENCODE_CONFIG"] == "/oc/config.json"
    assert not any(key.startswith("HARNESS_") for key in env)


# --- session id helper ------------------------------------------------------


def test_extract_session_id_accepts_dict_and_text() -> None:
    assert extract_session_id({"sessionID": "ses_x"}) == "ses_x"
    assert extract_session_id({"type": "error"}) is None
    text = "\n".join(
        json.dumps({"sessionID": sid})
        for sid in ("ses_first", "ses_last")
    )
    assert extract_session_id(text) == "ses_last"
    assert extract_session_id("garbage\nmore garbage") is None


def test_registry_and_full_config_ctor_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_API_KEY", "test-key")
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "led"))
    adapter = get("opencode", load_config())
    assert isinstance(adapter, OpencodeAdapter)
    with pytest.raises(ValueError, match="persist_dir"):
        OpencodeAdapter(OpencodeAgentsConfig())


# --- smoke (belt-and-braces; the live probe already ran manually) -----------


@pytest.mark.smoke
async def test_live_opencode_run(tmp_path: Path) -> None:
    if os.environ.get("HARNESS_SMOKE_OPENCODE") != "1":
        pytest.skip("manual: real opencode CLI (set HARNESS_SMOKE_OPENCODE=1)")
    adapter = OpencodeAdapter(load_config(), persist_dir=tmp_path)
    events = [
        event
        async for event in adapter.run(
            RunSpec(
                prompt="Reply with exactly: PROBE_OK",
                workdir="/app/projects/harness-smoke-r4",
                timeout_s=300,
            )
        )
    ]
    finals = [event for event in events if event.kind == "final"]
    assert finals and events[-1].kind != "failed"
    assert finals[0].session_ref is not None
