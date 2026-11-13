"""Tests for harness.supervisor (--check report, data tree idempotence, /healthz)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import subprocess
import sys
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from harness.supervisor import ensure_data_tree, main

_DATA_DIR_NAMES = ("memory", "skills", "sessions", "tasks/inbox", "state", "logs")
_SEED_FILES = ("skills/INDEX.md", "logs/harness.log", "state/budgets.json")
_CHECK_LINES = ("check: memory ok", "check: sessions ok", "check: skills ok", "check: tasks ok")


def _write_check_config(tmp_path: pathlib.Path) -> pathlib.Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text('api_key: "${HARNESS_API_KEY}"\n', encoding="utf-8")
    return config_file


def _snapshot_tree(
    data_dir: pathlib.Path,
) -> dict[str, tuple[bool, int, int]]:
    if not data_dir.is_dir():
        return {}
    snapshot: dict[str, tuple[bool, int, int]] = {}
    for base, _dir_names, file_names in os.walk(data_dir):
        for file_name in file_names:
            file_path = pathlib.Path(base, file_name)
            stat_result = file_path.stat()
            rel = file_path.relative_to(data_dir).as_posix()
            snapshot[rel] = (True, stat_result.st_size, stat_result.st_mtime_ns)
    return snapshot


def test_check_ok(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file = _write_check_config(tmp_path)
    exit_code = main(["--check", "--config", str(config_file)])
    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out
    assert output.startswith("config ok\ncheck: . ok\n")
    for rel in _DATA_DIR_NAMES:
        assert f"check: {rel} ok" in output
    for rel in _SEED_FILES:
        assert f"check: {rel} ok" in output
    assert captured.err == ""


def test_check_missing_api_key(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)
    config_file = _write_check_config(tmp_path)
    exit_code = main(["--check", "--config", str(config_file)])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "config error" in captured.err
    assert "HARNESS_API_KEY" in captured.err
    assert captured.out == ""


def test_tree_creation_idempotent(
    tmp_volume: pathlib.Path,
    cfg,
    supervisor_module,
) -> None:
    assert not tmp_volume.exists()
    ensure_data_tree(cfg)
    assert tmp_volume.is_dir()
    for rel in _DATA_DIR_NAMES:
        assert (tmp_volume / rel).is_dir()
    for rel in _SEED_FILES:
        assert (tmp_volume / rel).is_file()
    before = _snapshot_tree(tmp_volume)
    assert before
    supervisor_module.ensure_data_tree(cfg)
    assert _snapshot_tree(tmp_volume) == before


def test_budgets_file_is_single_json_line(tmp_volume: pathlib.Path, cfg) -> None:
    ensure_data_tree(cfg)
    budgets = tmp_volume / "state" / "budgets.json"
    content = budgets.read_text(encoding="utf-8")
    assert content == "{}\n"
    assert json.loads(content) == {}
    assert [leftover.name for leftover in budgets.parent.glob("*.tmp")] == []


def test_healthz_stub(supervisor_module) -> None:
    client = TestClient(supervisor_module.build_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.smoke
def test_serve_smoke(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    if os.environ.get("HARNESS_SMOKE_SUPERVISOR") != "1":
        pytest.skip("manual: uvicorn boot")
    exit_code = main(["--check", "--config", str(_write_check_config(tmp_path))])
    assert exit_code == 0


async def test_supervisor_start_bot_disabled_path(
    tmp_volume: pathlib.Path,
    cfg,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D18/R7 wiring: _start_bot is spawned exactly once when enabled+tokened,
    and short-circuits (never importing discord) when disabled at config or
    token level (cheap checks BEFORE the lazy import; spawn_bot re-checks)."""
    import harness.gateway.discord_bot as discord_module
    from harness.bus import Bus
    from harness.session_manager import SessionManager
    from harness.supervisor import _start_bot

    calls: list[object] = []

    async def _stub(cfg_arg, bus_arg, manager_arg, **_kwargs):
        calls.append(cfg_arg)

    monkeypatch.setattr(discord_module, "spawn_bot", _stub)
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

    assert await _start_bot(cfg, bus, manager) is None
    assert len(calls) == 1  # stub awaited once (enabled + tokened)

    disabled = cfg.model_copy(
        update={"discord": cfg.discord.model_copy(update={"enabled": False})}
    )
    assert await _start_bot(disabled, bus, manager) is None
    assert len(calls) == 1  # short-circuit: stub NOT called

    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    reenabled = cfg.model_copy(
        update={"discord": cfg.discord.model_copy(update={"enabled": True})}
    )
    assert await _start_bot(reenabled, bus, manager) is None
    assert len(calls) == 1  # token-unset: stub still NOT called


# ---------------------------------------------------------------------------
# WP3 (R9): TaskLoop wiring in _serve
# ---------------------------------------------------------------------------


def _drive_serve(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, str]]:
    """Patch the _serve seams (fake TaskLoop / create_app / uvicorn / recording
    bus) and return the shared event log so tests can assert boot + shutdown
    order."""
    import uvicorn

    import harness.gateway.app as gateway_app_module
    import harness.taskloop as taskloop_module
    from harness.bus import Bus

    events: list[tuple[object, str]] = []

    def record(actor: object, name: str) -> None:
        events.append((actor, name))

    class _FakeTaskLoop:
        instances: ClassVar[list[_FakeTaskLoop]] = []

        def __init__(self, cfg_arg, manager, bus=None, **_kwargs) -> None:
            self.cfg_arg = cfg_arg
            self.manager = manager
            self.bus = bus
            self.replay_calls = 0
            self.started = False
            self.stopped = False
            type(self).instances.append(self)

        def replay(self) -> dict[str, int]:
            self.replay_calls += 1
            record(self, "taskloop.replay")
            return {
                "tasks": 3,
                "requeued": 2,
                "failed_restarted": 1,
                "gate_restored": 4,
                "deps_waiting": 5,
            }

        def start(self) -> None:
            self.started = True
            record(self, "taskloop.start")

        async def stop(self) -> None:
            self.stopped = True
            record(self, "taskloop.stop")

    class _RecordingBus(Bus):
        async def aclose(self) -> None:
            record(self, "bus.aclose")
            await super().aclose()

    def _fake_create_app(cfg_arg, bus, manager):
        record(cfg_arg, "create_app")
        return object()

    class _FakeConfig:
        def __init__(self, app, **kwargs) -> None:
            self.app = app
            self.kwargs = kwargs

    class _FakeServer:
        def __init__(self, config) -> None:
            self.config = config

        async def serve(self) -> None:
            record(self, "serve")

    monkeypatch.setattr(taskloop_module, "TaskLoop", _FakeTaskLoop)
    monkeypatch.setattr(gateway_app_module, "create_app", _fake_create_app)
    monkeypatch.setattr("harness.supervisor.Bus", _RecordingBus)
    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    return events


def test_serve_wires_taskloop(
    tmp_volume: pathlib.Path,
    cfg,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WP3: _serve constructs the TaskLoop, replays task state ONCE before the
    gateway app is built, logs the replay counts, starts the loop, and stops it
    on server shutdown (stop is safe even when start never ran is taskloop's
    own contract, covered in WP2 tests)."""
    from harness.supervisor import _serve

    events = _drive_serve(monkeypatch)
    with caplog.at_level(logging.INFO, logger="harness.supervisor"):
        assert asyncio.run(_serve(cfg)) == 0
    import harness.taskloop as taskloop_module

    loop = taskloop_module.TaskLoop.instances[0]
    assert loop.replay_calls == 1
    assert loop.started and loop.stopped
    names = [name for _, name in events]
    assert names == [
        "taskloop.replay",
        "taskloop.start",
        "create_app",
        "serve",
        "taskloop.stop",
        "bus.aclose",
    ]
    assert events[names.index("create_app")][0] is cfg
    assert events[0][0] is loop  # replay recorded on the constructed loop
    assert (
        "tasks replayed: requeued=2 failed_restarted=1 gate_restored=4 deps_waiting=5"
        in caplog.text
    )


def test_serve_stops_taskloop_before_bus_close(
    tmp_volume: pathlib.Path,
    cfg,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP3 shutdown order: taskloop.stop() awaited before bus.aclose() (bot stop
    precedes both when a bot exists — here bot is None so only the taskloop/bus
    pair is observable)."""
    from harness.supervisor import _serve

    events = _drive_serve(monkeypatch)
    assert asyncio.run(_serve(cfg)) == 0
    names = [name for _, name in events]
    assert names.index("serve") < names.index("taskloop.stop")
    assert names.index("taskloop.stop") < names.index("bus.aclose")


def test_import_supervisor_does_not_import_taskloop() -> None:
    """WP3: importing harness.supervisor (and --check) stays taskloop-free; the
    import lives lazily inside _serve, mirroring the gateway lazy-import."""
    package_root = pathlib.Path(__file__).resolve().parents[1]
    code = "import harness.supervisor,sys; print('harness.taskloop' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        check=True,
    )
    assert result.stdout.strip() == "False"
