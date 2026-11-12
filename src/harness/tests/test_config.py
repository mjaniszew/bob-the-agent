"""Tests for harness.config.load_config (resolution order, fail-fast, template)."""

from __future__ import annotations

import pathlib

import pytest

from harness.config import HarnessConfig, load_config
from harness.errors import ConfigError, ConfigLoadError, ConfigValidationError

_TEMPLATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "harness.template.yaml"


def test_defaults(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert isinstance(cfg, HarnessConfig)
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8181
    assert cfg.rate_limit.per_minute == 30
    assert cfg.rate_limit.stream_per_minute == 600
    assert cfg.router.provider == "openai-compatible"
    assert cfg.router.base_url == "http://ollama:11434/v1"
    assert cfg.router.model == "glm-5.3-flash:cloud"
    assert cfg.router.api_key is None
    assert cfg.agents.hermes.profile_timeouts == {"simple": 900, "researcher": 3600}
    assert cfg.agents.hermes.timeout_s == 3600
    assert cfg.agents.opencode.timeout_s == 7200
    assert cfg.agents.opencode.workdir_root == "/app/projects"
    assert cfg.handoff.enabled is True
    assert cfg.handoff.max_depth == 3
    assert cfg.tasks.inbox == "tasks/inbox"
    assert cfg.tasks.poll_seconds == 2
    assert cfg.tasks.max_concurrent == 1
    assert cfg.tasks.gate_timeout_s == 86400
    assert cfg.scheduler.enabled is True
    assert cfg.scheduler.jobs_file == "state/scheduler.json"
    assert cfg.scheduler.misfire_grace_s == 60
    assert cfg.scheduler.coalesce is True
    assert cfg.scheduler.script_timeout_s == 600
    assert cfg.scheduler.jobs == []
    assert cfg.memory.budgets.memory_chars == 2200
    assert cfg.memory.budgets.user_chars == 1375
    assert cfg.memory.budgets.lessons_chars == 1500
    assert cfg.memory.budgets.index_chars == 1500
    assert cfg.discord.chunk_ms == 700
    assert cfg.discord.chunk_chars == 1900
    assert cfg.discord.home_channel_id is None
    assert cfg.storage.data_dir == str(tmp_volume)


def test_env_override_wins(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml_data_dir = tmp_path / "yaml-data"
    env_data_dir = tmp_path / "env-data"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"server:\n  host: 127.0.0.1\n  port: 7000\n"
        f"storage:\n  data_dir: '{yaml_data_dir}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_PORT", "8182")
    monkeypatch.setenv("HARNESS_DATA_DIR", str(env_data_dir))
    monkeypatch.setenv("HARNESS_ROUTER_MODEL", "env-model:latest")
    monkeypatch.setenv("HARNESS_WORKDIR", str(tmp_path / "projects"))
    monkeypatch.setenv("HARNESS_DISCORD_HOME_CHANNEL", "424242")
    cfg = load_config(str(config_file))
    assert cfg.server.port == 8182
    assert cfg.storage.data_dir == str(env_data_dir)
    assert cfg.router.model == "env-model:latest"
    assert cfg.agents.opencode.workdir_root == str(tmp_path / "projects")
    assert cfg.discord.home_channel_id == "424242"
    assert cfg.server.host == "127.0.0.1"


def test_discord_home_channel_fallback(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("discord:\n  home_channel_id: null\n", encoding="utf-8")
    monkeypatch.setenv("DISCORD_HOME_CHANNEL", "51505150")
    cfg = load_config(str(config_file))
    assert cfg.discord.home_channel_id == "51505150"


def test_bad_yaml_readable_error(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("server: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError, match=r"(?i)yaml|parse") as excinfo:
        load_config(str(config_file))
    assert excinfo.value.key == str(config_file)
    assert str(config_file) in str(excinfo.value)


def test_missing_explicit_config_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ConfigLoadError, match="does not exist"):
        load_config(str(tmp_path / "nope.yaml"))


def test_var_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'api_key: "${MY_TOKEN}"\nrouter:\n  base_url: "http://${OLLAMA_HOST}/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_TOKEN", "abc")
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.9")
    cfg = load_config(str(config_file))
    assert cfg.api_key == "abc"
    assert cfg.router.base_url == "http://10.0.0.9/v1"


def test_unresolved_var_fail_fast(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_TOKEN", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text('api_key: "${MY_TOKEN}"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_file))
    message = str(excinfo.value)
    assert "unset" in message
    assert "MY_TOKEN" in message


def test_empty_env_api_key_fails_fast(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "HARNESS_API_KEY" in message
    assert "empty" in message


def test_empty_yaml_api_key_fails_fast(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text('api_key: ""\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_file))
    message = str(excinfo.value)
    assert "HARNESS_API_KEY" in message
    assert "empty" in message


def test_unknown_key_rejected(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("bogus_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as excinfo:
        load_config(str(config_file))
    assert isinstance(excinfo.value, ValueError)
    assert "bogus_key" in str(excinfo.value)


def test_template_yaml_loads(tmp_volume: pathlib.Path) -> None:
    cfg = load_config(str(_TEMPLATE_PATH))
    assert isinstance(cfg, HarnessConfig)
    assert cfg.server.port == 8181
    assert cfg.router.api_key is None
    assert cfg.discord.home_channel_id is None
