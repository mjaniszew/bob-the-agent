"""Tests for harness.adapters.hermes (argv snapshots, env allowlist, relay
filtering, session ids, watchdog, ledger)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from harness.adapters import get
from harness.adapters.base import RunSpec, build_child_env
from harness.adapters.hermes import (
    HermesAdapter,
    extract_session_id,
    newest_session_id_from_store,
)
from harness.config import HermesAgentsConfig, load_config
from harness.persist import read_jsonl
from tests.adapters_conftest import (
    FakeProc,
    fixture_lines,
    install_fake_start_process,
    qf_masked,
)

_BIN = "/opt/hermes/.venv/bin/hermes"
_FIXTURES = Path(__file__).parent / "fixtures"


def _sub_config(tmp_path: Path, **overrides: object) -> HermesAgentsConfig:
    values: dict[str, object] = {
        "bin": _BIN,
        "default_model": None,
        "timeout_s": 3600,
        "profile_timeouts": {"simple": 900},
    }
    values.update(overrides)
    return HermesAgentsConfig(**values)  # type: ignore[arg-type]


def _adapter(tmp_path: Path, **overrides: object) -> HermesAdapter:
    return HermesAdapter(_sub_config(tmp_path, **overrides), persist_dir=tmp_path)


def _argv_budget(argv: list[str]) -> str:
    return argv[argv.index("--run-budget") + 1]


def _pinned_sleep_argv(spec: RunSpec, qf: str, timeout: int) -> list[str]:
    """F2 seam: pin the argv so the spawned child is a plain `/bin/sleep 30`."""
    return ["/bin/sleep", "30"]


def _model_arg(argv: list[str]) -> str:
    return argv[argv.index("-m") + 1]


# --- argv snapshot matrix (exact list equality) ---------------------------


async def test_argv_snapshot_profile_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile run argv (task Appendix A): -p, chat --oneshot --query-file,
    --run-budget=profile_timeouts['simple']=900; no -m (default_model None), no
    -t/--reasoning/--resume/--in. Spawn cwd defaults to the harness cwd."""
    adapter = _adapter(tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    events = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    argv = calls[0]["argv"]
    assert qf_masked(argv) == [
        _BIN,
        "-p",
        "simple",
        "chat",
        "--oneshot",
        "--query-file",
        "<QF>",
        "--no-restore-cwd",
        "--source",
        "harness",
        "--run-budget",
        "900",
    ]
    assert Path(argv[argv.index("--query-file") + 1]).name.startswith("harness-query-")
    assert calls[0]["cwd"] == os.getcwd()
    assert [event.kind for event in events] == []


async def test_argv_snapshot_profile_run_full_featured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model/toolsets/reasoning/resume/workdir all land in their exact slots."""
    adapter = HermesAdapter(
        _sub_config(tmp_path, default_model="glm-test-x"),
        persist_dir=tmp_path,
        toolsets="core,web",
        reasoning_level="low",
    )
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    spec = RunSpec(
        prompt="hi",
        profile="simple",
        resume_ref="20261112_223916_4f2531",
        workdir="/app/projects/demo",
    )
    _ = [event async for event in adapter.run(spec)]
    assert calls[0]["cwd"] == "/app/projects/demo"
    assert qf_masked(calls[0]["argv"]) == [
        _BIN,
        "-p",
        "simple",
        "chat",
        "--oneshot",
        "--query-file",
        "<QF>",
        "-m",
        "glm-test-x",
        "-t",
        "core,web",
        "--reasoning",
        "low",
        "--resume",
        "20261112_223916_4f2531",
        "--in",
        "/app/projects/demo",
        "--no-restore-cwd",
        "--source",
        "harness",
        "--run-budget",
        "900",
    ]


async def test_argv_snapshot_non_profile_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NON-profile run (profile=None): NO '-p' anywhere in argv — the task
    contract omits -p entirely (the hermes CLI falls back to its default
    profile); the run budget falls back to the config timeout (3600)."""
    adapter = _adapter(tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="plain"))]
    argv = qf_masked(calls[0]["argv"])
    assert "-p" not in argv
    assert argv == [
        _BIN,
        "chat",
        "--oneshot",
        "--query-file",
        "<QF>",
        "--no-restore-cwd",
        "--source",
        "harness",
        "--run-budget",
        "3600",
    ]


async def test_timeout_resolution_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec.timeout_s overrides both profile_timeouts and the config timeout;
    profile_timeouts lookup falls back to the config timeout."""
    adapter = _adapter(tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="a", profile="simple"))]
    assert _argv_budget(calls[0]["argv"]) == "900"  # profile_timeouts lookup
    _ = [event async for event in adapter.run(RunSpec(prompt="b", timeout_s=123))]
    assert _argv_budget(calls[1]["argv"]) == "123"  # spec override wins
    _ = [
        event
        async for event in adapter.run(RunSpec(prompt="c", profile="unknown-profile"))
    ]
    assert _argv_budget(calls[2]["argv"]) == "3600"  # fallback to config timeout


async def test_model_resolves_spec_over_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path, default_model="cfg-model")
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="a", model="spec-model"))]
    assert _model_arg(calls[0]["argv"]) == "spec-model"
    _ = [event async for event in adapter.run(RunSpec(prompt="b"))]
    assert _model_arg(calls[1]["argv"]) == "cfg-model"


# --- child env allowlist (M-3) ---------------------------------------------


def test_build_child_env_m3_negative() -> None:
    """HARNESS_API_KEY/ROUTER_* are all absent even when present in base env."""
    poisoned = dict(os.environ)
    poisoned.update(
        {
            "PATH": "/bin",
            "HOME": "/root",
            "TERM": "xterm",
            "HARNESS_API_KEY": "secret",
            "HARNESS_ROUTER_BASE_URL": "http://router.example",
            "HARNESS_ROUTER_MODEL": "glm-x",
            "HARNESS_ROUTER_API_KEY": "router-secret",
        }
    )
    env = build_child_env(
        poisoned,
        {"HERMES_HOME": "/opt/data"},
        keep=("PATH", "HOME", "TERM", "HERMES_HOME"),
    )
    assert env["HERMES_HOME"] == "/opt/data"
    for kept in ("PATH", "HOME", "TERM"):
        if kept in os.environ:
            assert env[kept] == poisoned[kept]
    for denied in (
        "HARNESS_API_KEY",
        "HARNESS_ROUTER_BASE_URL",
        "HARNESS_ROUTER_MODEL",
        "HARNESS_ROUTER_API_KEY",
    ):
        assert denied not in env


async def test_spawn_env_allowlist_and_profile_passthrough(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real _spawn path: allowlisted base env + HERMES_HOME + profile_env."""
    monkeypatch.setenv("HARNESS_API_KEY", "topsecret")
    monkeypatch.setenv("HARNESS_ROUTER_MODEL", "glm-x")
    monkeypatch.setenv("HERMES_HOME", "/opt/data")
    monkeypatch.setenv("R4_ENV_SHOULD_NOT_PASS", "1")
    adapter = HermesAdapter(
        _sub_config(tmp_path),
        persist_dir=tmp_path,
        profile_env={"R4_PROFILE_PASSTHROUGH": "v"},
    )
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    env = calls[0]["env"]
    assert env["HERMES_HOME"] == "/opt/data"
    assert env["R4_PROFILE_PASSTHROUGH"] == "v"
    for kept in ("PATH", "HOME", "TERM"):
        if kept in os.environ:
            assert env[kept] == os.environ[kept]
    assert "R4_ENV_SHOULD_NOT_PASS" not in env
    assert not any(key.startswith("HARNESS_") for key in env)


def test_registry_and_full_config_ctor_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get('hermes', cfg) returns a HermesAdapter built from a full config
    (ledger rooted at storage.data_dir); the bare sub-config path needs an
    explicit persist_dir."""
    monkeypatch.setenv("HARNESS_API_KEY", "test-key")
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "led"))
    adapter = get("hermes", load_config())
    assert isinstance(adapter, HermesAdapter)
    assert isinstance(_adapter(tmp_path), HermesAdapter)
    with pytest.raises(ValueError, match="persist_dir"):
        HermesAdapter(HermesAgentsConfig())


# --- session id extraction --------------------------------------------------


def test_extract_session_id_from_recorded_fixtures() -> None:
    stdout_text = (_FIXTURES / "hermes-oneshot-stdout.txt").read_text(encoding="utf-8")
    assert extract_session_id(stdout_text) == "20261112_223916_4f2531"
    resume_text = (_FIXTURES / "hermes-resume-probe.out").read_text(encoding="utf-8")
    assert extract_session_id(resume_text) == "20261112_223916_4f2531"
    assert extract_session_id("no session marker at all\n") is None


def test_newest_session_id_from_store(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profiles" / "simple"
    profile_dir.mkdir(parents=True)
    connection = sqlite3.connect(profile_dir / "state.db")
    connection.execute("CREATE TABLE sessions (id TEXT, started_at REAL)")
    connection.execute("INSERT INTO sessions VALUES ('earlier', 100.0)")
    connection.execute("INSERT INTO sessions VALUES ('later', 500.0)")
    connection.commit()
    connection.close()
    assert newest_session_id_from_store(profile_dir) == "later"

    empty_dir = tmp_path / "profiles" / "empty"
    empty_dir.mkdir(parents=True)
    empty_db = sqlite3.connect(empty_dir / "state.db")
    empty_db.execute("CREATE TABLE sessions (id TEXT, started_at REAL)")
    empty_db.commit()
    empty_db.close()
    assert newest_session_id_from_store(empty_dir) is None
    assert newest_session_id_from_store(tmp_path / "profiles" / "missing") is None


def test_profile_dir_rejects_unsafe_profile_names(tmp_path: Path) -> None:
    """The sqlite store path is built ONLY from ``^[A-Za-z0-9_-]+$`` names:
    traversal (``../etc``) and path separators (``a/b``) return None — the
    sqlite fallback is skipped rather than escaping $HERMES_HOME/profiles —
    while real names like 'simple' resolve normally."""
    adapter = HermesAdapter(
        _sub_config(tmp_path), persist_dir=tmp_path, hermes_home=str(tmp_path)
    )
    assert adapter._profile_dir("../etc") is None
    assert adapter._profile_dir("a/b") is None
    assert adapter._profile_dir("simple") == tmp_path / "profiles" / "simple"


async def test_session_store_fallback_when_stdout_has_no_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_dir = tmp_path / "profiles" / "simple"
    profile_dir.mkdir(parents=True)
    connection = sqlite3.connect(profile_dir / "state.db")
    connection.execute("CREATE TABLE sessions (id TEXT, started_at REAL)")
    connection.execute("INSERT INTO sessions VALUES ('store-session-id', 42.0)")
    connection.commit()
    connection.close()
    adapter = HermesAdapter(
        _sub_config(tmp_path),
        persist_dir=tmp_path,
        hermes_home=str(tmp_path),
    )
    calls = install_fake_start_process(
        monkeypatch, FakeProc([b"RESULT line with no session footer\r\n"], b"", 0)
    )
    events = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    assert [event.kind for event in events] == ["chunk"]  # success, no failed event
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert extract_session_id("RESULT line with no session footer") is None
    assert row["session_ref"] == "store-session-id"  # sqlite fallback answered
    assert row["argv_redacted"] == calls[0]["argv"]


async def test_session_id_resume_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session-id -> resume composition across TWO adapter.run calls: run 1's
    fake stdout carries the ``Session:        20261112_223916_4f2531`` footer
    -> ledger ``session_ref``; run 2 fed that ``resume_ref`` lands the
    ``--resume 20261112_223916_4f2531`` argv slot."""
    adapter = _adapter(tmp_path)
    from harness.adapters import base as base_module

    calls: list[dict[str, object]] = []
    stdout_lines = [b"PROBE_OK\r\n", b"Session:        20261112_223916_4f2531\r\n"]

    async def fake_start_process(argv, cwd, env):  # fresh proc per run
        calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return FakeProc(list(stdout_lines), b"", 0), True

    monkeypatch.setattr(base_module, "start_process", fake_start_process)
    _ = [event async for event in adapter.run(RunSpec(prompt="go"))]
    row1 = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row1["session_ref"] == "20261112_223916_4f2531"
    session_ref = str(row1["session_ref"])
    _ = [
        event
        async for event in adapter.run(RunSpec(prompt="go", resume_ref=session_ref))
    ]
    argv2 = list(calls[1]["argv"])
    assert argv2[argv2.index("--resume") : argv2.index("--resume") + 2] == [
        "--resume",
        "20261112_223916_4f2531",
    ]


# --- relay filtering (LINE_FILTERS; faithful relay otherwise) ---------------


async def test_relay_filters_fixture_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture-driven relay: banner/box/echo lines filtered, content kept."""
    adapter = _adapter(tmp_path)
    install_fake_start_process(
        monkeypatch, FakeProc(fixture_lines("hermes-oneshot-stdout.txt"), b"", 0)
    )
    events = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    assert [(event.kind, event.text) for event in events] == [
        ("chunk", "PROBE_OK"),
        ("chunk", "Resume this session with:"),
        ("chunk", "  hermes --resume 20261112_223916_4f2531 -p simple"),
        ("chunk", "Session:        20261112_223916_4f2531"),
        ("chunk", "Duration:       9s"),
        ("chunk", "Messages:       2 (1 user, 0 tool calls)"),
    ]


async def test_relay_resumes_with_help_and_title_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume help + footers relayed faithfully (no line is lost silently)."""
    adapter = _adapter(tmp_path)
    install_fake_start_process(
        monkeypatch, FakeProc(fixture_lines("hermes-resume-probe.out"), b"", 0)
    )
    events = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    assert [(event.kind, event.text) for event in events] == [
        ("chunk", "↻ Resumed session 20261112_223916_4f2531 (1 user message, 2 total messages)"),
        ("chunk", "CONT_OK"),
        ("chunk", "Resume this session with:"),
        ("chunk", "  hermes --resume 20261112_223916_4f2531 -p simple"),
        ("chunk", '  hermes -c "Continue and say CONT_OK" -p simple'),
        ("chunk", "Session:        20261112_223916_4f2531"),
        ("chunk", "Title:          Continue and say CONT_OK"),
        ("chunk", "Duration:       8s"),
        ("chunk", "Messages:       4 (2 user, 0 tool calls)"),
    ]


# --- failures, watchdog, ledger ---------------------------------------------


async def test_f1_nonzero_exit_maps_to_failed_with_stderr_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    stderr = b"X" * 300 + b"ERROR boom: something went very wrong" + b"Z" * 300
    calls = install_fake_start_process(
        monkeypatch,
        FakeProc(
            [b"PROBE_OK\r\n", b"Session:        20261112_223916_4f2531\r\n"],
            stderr_chunks=stderr,
            exit_code=3,
        ),
    )
    events = [event async for event in adapter.run(RunSpec(prompt="x", profile="simple"))]
    assert events[0].kind == "chunk" and events[0].text == "PROBE_OK"
    failed = events[-1]
    assert failed.kind == "failed"
    assert failed.exit_code == 3
    tail = stderr.decode()[-500:]
    assert len(tail) == 500
    assert (failed.error or "").startswith("hermes exited with code 3: ")
    assert failed.error == f"hermes exited with code 3: {tail}"
    assert "something went very wrong" in (failed.error or "")
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row["agent"] == "hermes"
    assert row["argv_redacted"] == calls[0]["argv"]
    assert row["exit_code"] == 3
    assert row["session_ref"] == "20261112_223916_4f2531"
    assert row["error"].startswith("hermes exited with code 3")
    assert "duration_s" in row and "ts" in row


async def test_f1_success_row_has_no_error_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    install_fake_start_process(monkeypatch, FakeProc([b"PROBE_OK\r\n"], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="x"))]
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row["exit_code"] == 0
    assert "error" not in row


async def test_f2_watchdog_timeout_kills_sleeping_child(tmp_path: Path) -> None:
    """Real /bin/sleep 30 with a 1s budget: watchdog killpg -> failed event.

    Uses the real spawn path (no fake); argv is pinned via the _build_argv seam
    so the child is a plain sleep and the timeout machinery is exercised for
    real (`asyncio.timeout` + `kill_process_group`, plan F2).
    """
    adapter = _adapter(tmp_path)
    adapter._build_argv = _pinned_sleep_argv  # type: ignore[method-assign]
    started = time.monotonic()
    events = [event async for event in adapter.run(RunSpec(prompt="ignored", timeout_s=1))]
    elapsed = time.monotonic() - started
    assert elapsed < 5, "watchdog must not wait out the child's full sleep"
    assert [(event.kind, event.error) for event in events] == [("failed", "timeout after 1s")]
    assert events[-1].exit_code is None
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert row["error"] == "timeout after 1s"
    assert row["exit_code"] == -15  # SIGTERM reaped the sleep immediately


async def test_f12_missing_bin_maps_to_failed(tmp_path: Path) -> None:
    adapter = HermesAdapter(
        _sub_config(tmp_path, bin="/opt/definitely-not-here/hermes"),
        persist_dir=tmp_path,
    )
    events = [
        event
        async for event in adapter.run(RunSpec(prompt="x", profile="simple", timeout_s=5))
    ]
    failed = events[-1]
    assert events == [failed]
    assert failed.kind == "failed"
    assert failed.exit_code is None
    assert "failed to spawn hermes '/opt/definitely-not-here/hermes'" in (failed.error or "")
    assert "No such file" in (failed.error or "")
    row = read_jsonl(tmp_path / "runs_ledger.jsonl")[-1]
    assert (row["error"] or "").startswith("failed to spawn hermes")


async def test_prompt_travels_via_query_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt never touches argv (query-file beats argv for long prompts)."""
    adapter = _adapter(tmp_path)
    calls = install_fake_start_process(monkeypatch, FakeProc([], b"", 0))
    _ = [event async for event in adapter.run(RunSpec(prompt="long " * 500, profile="simple"))]
    assert "long" not in json.dumps(calls[0]["argv"])


# --- smoke (belt-and-braces; the live probes already ran manually) ----------


@pytest.mark.smoke
async def test_live_hermes_oneshot(tmp_path: Path) -> None:
    if os.environ.get("HARNESS_SMOKE_HERMES") != "1":
        pytest.skip("manual: real hermes CLI (set HARNESS_SMOKE_HERMES=1)")
    adapter = HermesAdapter(
        HermesAgentsConfig(
            profile_timeouts={"simple": 300},
            timeout_s=300,
            default_model=None,
        ),
        persist_dir=tmp_path,
    )
    events = [
        event
        async for event in adapter.run(
            RunSpec(prompt="Reply with exactly: PROBE_OK", profile="simple", timeout_s=300)
        )
    ]
    transcript = "\n".join(event.text or "" for event in events if event.kind == "chunk")
    assert events[-1].kind != "failed"
    assert extract_session_id(transcript) is not None
