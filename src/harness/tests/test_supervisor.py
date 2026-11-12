"""Tests for harness.supervisor (--check report, data tree idempotence, /healthz)."""

from __future__ import annotations

import json
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from harness.supervisor import build_app, ensure_data_tree, main

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
