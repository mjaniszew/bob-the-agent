"""Tests for bob_harness.config.load_config (resolution order, fail-fast, template)."""

from __future__ import annotations

import pathlib

import pytest

from bob_harness.config import HarnessConfig, SchedulerConfig, load_config
from bob_harness.errors import ConfigError, ConfigLoadError, ConfigValidationError

_TEMPLATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "bob-harness.template.yaml"


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
    assert cfg.agents.hermes.default_profile == "simple"
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
    assert cfg.scheduler.env_allowlist == []
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
    monkeypatch.setenv("BOB_HARNESS_PORT", "8182")
    monkeypatch.setenv("BOB_HARNESS_DATA_DIR", str(env_data_dir))
    monkeypatch.setenv("BOB_HARNESS_ROUTER_MODEL", "env-model:latest")
    monkeypatch.setenv("BOB_HARNESS_WORKDIR", str(tmp_path / "projects"))
    monkeypatch.setenv("BOB_HARNESS_DISCORD_HOME_CHANNEL", "424242")
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
    monkeypatch.delenv("BOB_HARNESS_API_KEY", raising=False)
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
    monkeypatch.setenv("BOB_HARNESS_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "BOB_HARNESS_API_KEY" in message
    assert "empty" in message


def test_empty_yaml_api_key_fails_fast(tmp_path: pathlib.Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text('api_key: ""\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_file))
    message = str(excinfo.value)
    assert "BOB_HARNESS_API_KEY" in message
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


def test_scheduler_config_env_allowlist_default_and_override() -> None:
    """R11 WP2: env_allowlist defaults to an empty list and validates as a
    list of strings when set (YAML-only field; no flat env binding)."""
    model = SchedulerConfig()
    assert model.env_allowlist == []
    override = SchedulerConfig(env_allowlist=["FOO"])
    assert override.env_allowlist == ["FOO"]


# ---------------------------------------------------------------------------
# P2: harness: capability-configuration group + flat env bindings + runner
# ---------------------------------------------------------------------------


def test_harness_group_defaults(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Part 1 §3.4 harness: YAML block parses into HarnessHarnessConfig."""
    from bob_harness.config import HarnessHarnessConfig

    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """\
harness:
  memory:
    enabled: false
    dir: /data/mem
    workspace_root: /data/mem-ws
    agent_name: custom-executor
    inject: false
    max_tokens: 1000
  skills:
    enabled: false
    include: [alpha]
    exclude: [beta]
    read_files: true
    missing_directories: error
  ask_user:
    enabled: false
    id: ask
    answerer: inline
    timeout_s: 30
  subagents:
    enabled: false
    max_depth: 2
  delegation:
    enabled: false
    tool_name: delegate
""",
        encoding="utf-8",
    )
    cfg = load_config(str(config_file))
    g = cfg.harness
    assert isinstance(g, HarnessHarnessConfig)
    assert g.memory.enabled is False
    assert g.memory.dir == "/data/mem"
    assert g.memory.workspace_root == "/data/mem-ws"
    assert g.memory.agent_name == "custom-executor"
    assert g.memory.inject is False
    assert g.memory.max_tokens == 1000
    assert g.skills.enabled is False
    assert g.skills.include == ["alpha"]
    assert g.skills.exclude == ["beta"]
    assert g.skills.read_files is True
    assert g.skills.missing_directories == "error"
    assert g.ask_user.enabled is False
    assert g.ask_user.id == "ask"
    assert g.ask_user.answerer == "inline"
    assert g.ask_user.timeout_s == 30
    assert g.subagents.enabled is False
    assert g.subagents.max_depth == 2
    assert g.delegation.enabled is False
    assert g.delegation.tool_name == "delegate"
    # tasks.runner: Literal["analyzed","executor"], default "analyzed" (P7).
    assert cfg.tasks.runner == "analyzed"


def test_harness_group_yaml_defaults(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shipped defaults (no YAML): every §3.4 toggle parses at enabled:true."""
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None)
    g = cfg.harness
    assert g.memory.enabled is True
    assert g.memory.dir == ""
    assert g.memory.workspace_root == ""
    assert g.memory.agent_name == "executor"
    assert g.memory.inject is True
    assert g.memory.max_tokens == 2000
    assert g.skills.enabled is True
    assert g.skills.include == []
    assert g.skills.exclude == []
    assert g.skills.read_files is False
    assert g.skills.missing_directories == "skip"
    assert g.ask_user.enabled is True
    assert g.ask_user.id == "ask_user"
    assert g.ask_user.answerer == "deferred"
    assert g.ask_user.timeout_s is None
    assert g.subagents.enabled is True
    assert g.subagents.max_depth == 3
    assert g.delegation.enabled is True
    assert g.delegation.tool_name == "delegate_run"


def test_new_flat_env_bindings(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 15 §3.4 flat env names map through _FLAT_ENV_BINDINGS."""

    def _binding(env_var: str) -> str:
        from bob_harness.config import _FLAT_ENV_BINDINGS

        for name, dotted in _FLAT_ENV_BINDINGS:
            if name == env_var:
                return dotted
        raise AssertionError(f"{env_var} not in _FLAT_ENV_BINDINGS")

    expected = {
        "BOB_HARNESS_MEMORY_ENABLED": "harness.memory.enabled",
        "BOB_HARNESS_MEMORY_DIR": "harness.memory.dir",
        "BOB_HARNESS_MEMORY_WORKSPACE": "harness.memory.workspace_root",
        "BOB_HARNESS_MEMORY_AGENT_NAME": "harness.memory.agent_name",
        "BOB_HARNESS_MEMORY_INJECT": "harness.memory.inject",
        "BOB_HARNESS_MEMORY_MAX_TOKENS": "harness.memory.max_tokens",
        "BOB_HARNESS_SKILLS_ENABLED": "harness.skills.enabled",
        "BOB_HARNESS_SKILLS_READ_FILES": "harness.skills.read_files",
        "BOB_HARNESS_SKILLS_MISSING": "harness.skills.missing_directories",
        "BOB_HARNESS_ASK_USER_ENABLED": "harness.ask_user.enabled",
        "BOB_HARNESS_ASK_USER_ANSWERER": "harness.ask_user.answerer",
        "BOB_HARNESS_ASK_USER_TIMEOUT_S": "harness.ask_user.timeout_s",
        "BOB_HARNESS_SUBAGENTS_ENABLED": "harness.subagents.enabled",
        "BOB_HARNESS_SUBAGENTS_MAX_DEPTH": "harness.subagents.max_depth",
        "BOB_HARNESS_DELEGATION_ENABLED": "harness.delegation.enabled",
    }
    for env_var, dotted in expected.items():
        assert _binding(env_var) == dotted
    # And they BIND live: env beats YAML/defaults for one representative path.
    monkeypatch.setenv("BOB_HARNESS_MEMORY_ENABLED", "false")
    monkeypatch.setenv("BOB_HARNESS_MEMORY_AGENT_NAME", "env-executor")
    cfg = load_config(None)
    assert cfg.harness.memory.enabled is False
    assert cfg.harness.memory.agent_name == "env-executor"


def test_unknown_env_forbidden(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra="forbid" stays intact: untouched nested keys still fail loudly."""
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "harness:\n  memory:\n    not_a_real_key: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError) as excinfo:
        load_config(str(config_file))
    assert "not_a_real_key" in str(excinfo.value)
    # Deeply unknown top-level groups still forbidden too.
    config_file.write_text("harness:\n  bogus_group: {}\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as excinfo:
        load_config(str(config_file))
    assert "bogus_group" in str(excinfo.value)


def test_tasks_runner_env_binding(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOB_HARNESS_TASKS_RUNNER maps to tasks.runner (P7's flip switch)."""
    monkeypatch.setenv("BOB_HARNESS_TASKS_RUNNER", "executor")
    cfg = load_config(None)
    assert cfg.tasks.runner == "executor"
    monkeypatch.setenv("BOB_HARNESS_TASKS_RUNNER", "analyzed")
    cfg = load_config(None)
    assert cfg.tasks.runner == "analyzed"


def test_tasks_runner_rejects_unknown(
    tmp_volume: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tasks.runner is a Literal: unknown values fail validation."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("tasks:\n  runner: bogus\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as excinfo:
        load_config(str(config_file))
    assert "runner" in str(excinfo.value)
