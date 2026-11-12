"""Harness configuration schema and loader (ARCHITECTURE.md section 4).

This module defines the full harness config model tree plus
:func:`load_config`, the single entry point that turns a YAML file (+ the
environment) into a validated :class:`HarnessConfig`.

Resolution order (applied in exactly this order):

1. **YAML file** — ``load_config(path)`` uses ``path`` when given, else the
   file named by ``$HARNESS_CONFIG`` when that env var is set, else
   ``./config.yaml``. An explicitly configured file (``path`` arg or
   ``$HARNESS_CONFIG``) that does not exist or contains invalid YAML raises
   :class:`~harness.errors.ConfigLoadError` naming the path (plus the YAML
   error line/column when available). If ``$HARNESS_CONFIG`` is unset and
   ``./config.yaml`` is absent, the built-in defaults are used and no YAML
   file is required at all.
2. **Deep merge** — the YAML mapping (if any) is deep-merged onto the
   built-in defaults (``_BUILTIN_DEFAULTS``): nested mappings merge
   recursively, scalars and lists replace wholesale.
3. **``${VAR}`` resolution** — every string leaf of the merged mapping is
   scanned recursively for ``${NAME}`` placeholders and each is replaced from
   ``os.environ``. Referencing an unset or empty variable fails fast with
   :class:`~harness.errors.ConfigError` naming the config key, e.g.
   ``api_key references ${HARNESS_API_KEY} which is unset``. Resolution
   happens after the YAML merge, and before env overrides and before model
   validation.
4. **Env overrides** — applied via pydantic-settings
   (``env_prefix="HARNESS_"``) using the nested-settings source
   :class:`_FlatEnvSource`: ``HARNESS_HOST`` -> ``server.host``,
   ``HARNESS_PORT`` -> ``server.port``, ``HARNESS_API_KEY`` -> ``api_key``,
   ``HARNESS_DATA_DIR`` -> ``storage.data_dir``,
   ``HARNESS_ROUTER_BASE_URL`` -> ``router.base_url``,
   ``HARNESS_ROUTER_MODEL`` -> ``router.model``, ``HARNESS_WORKDIR`` ->
   ``agents.opencode.workdir_root``, and ``HARNESS_DISCORD_HOME_CHANNEL`` ->
   ``discord.home_channel_id`` with fallback to plain ``DISCORD_HOME_CHANNEL``
   when the former is unset. Every other nested value comes from the
   YAML/defaults merge (not from the environment).
5. **Validation** — pydantic models, ``extra="forbid"`` on every model with
   assignment validation off: unknown YAML keys (including a stray top-level
   key) fail loudly with a readable error naming the offending key instead of
   being silently ignored.

Repo reconciliations baked into the defaults below (superseding the original
ARCHITECTURE.md design values):

(a) ``storage.data_dir`` defaults to ``/opt/data/harness`` (design said
    ``/data/harness`` — superseded); overridable via ``HARNESS_DATA_DIR``.
(b) ``router`` defaults to the ``openai-compatible`` provider pointed at the
    ollama gateway (``http://ollama:11434/v1``, model
    ``glm-5.3-flash:cloud``) — user decision Q3; the design's zai row is
    superseded.
(c) ``server.port`` is ``8181`` everywhere (user decision Q1; the plan's
    8080 is superseded).
(d) ``discord.home_channel_id`` (user decision Q4): Discord notices without
    an originating thread go to the home channel; env
    ``HARNESS_DISCORD_HOME_CHANNEL`` (fallback ``DISCORD_HOME_CHANNEL``).

No side effects on import (no ``__main__`` block; only constants are defined
at module load).
"""

from __future__ import annotations

import os
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from harness.errors import ConfigError, ConfigLoadError, ConfigValidationError

__all__ = [
    "ServerConfig",
    "RateLimitConfig",
    "RouterConfig",
    "AgentsConfig",
    "HermesAgentsConfig",
    "OpencodeAgentsConfig",
    "HandoffConfig",
    "TasksConfig",
    "SchedulerConfig",
    "MemoryBudgetsConfig",
    "MemoryConfig",
    "DiscordConfig",
    "DiscordAllowlist",
    "StorageConfig",
    "HarnessConfig",
    "load_config",
    "effective_router_settings",
]

# ${NAME} placeholder pattern scanned in every string leaf of the merged config.
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_DEFAULT_CONFIG_FILE = "config.yaml"
_HARNESS_CONFIG_ENV = "HARNESS_CONFIG"

# Belt-and-braces guard message raised when api_key is missing/empty after all
# resolution (YAML ${VAR} templates, env overrides, validator pass).
_API_KEY_EMPTY_MESSAGE = (
    "HARNESS_API_KEY is empty: set it in the environment (openssl rand -hex 32) or in config"
)

# Built-in defaults for every model field (ARCHITECTURE.md section 4, per the
# repo reconciliations (a)-(d) documented in the module docstring). ``api_key``
# is intentionally absent: it has no usable default and must come from the
# environment (HARNESS_API_KEY) or YAML.
_BUILTIN_DEFAULTS: dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 8181},  # reconciliation (c): port 8181 (Q1)
    "rate_limit": {"per_minute": 30, "stream_per_minute": 600},
    "router": {
        "provider": "openai-compatible",  # reconciliation (b): Q3, zai row superseded
        "base_url": "http://ollama:11434/v1",
        "model": "glm-5.3-flash:cloud",
        "fallback_model": None,
        "max_retries": 1,
        "temperature": 0,
        "timeout_s": 20,
        "api_key": None,
    },
    "agents": {
        "hermes": {
            "enabled": True,
            "bin": "/opt/hermes/.venv/bin/hermes",
            "default_model": None,
            "timeout_s": 3600,  # plan section 10: chat raised to researcher parity (600 superseded)
            "max_output_bytes": 4_000_000,
            "profile_timeouts": {"simple": 900, "researcher": 3600},  # plan section 10 parity
        },
        "opencode": {
            "enabled": True,
            "bin": "opencode",
            "default_model": None,
            "default_agent": "build",
            "timeout_s": 7200,  # plan section 10: 120m parity (900 superseded)
            "workdir_root": "/app/projects",
            "auto_approve": True,
        },
    },
    "handoff": {"enabled": True, "max_depth": 3},
    "tasks": {
        "inbox": "tasks/inbox",
        "poll_seconds": 2,
        "max_concurrent": 1,
        "max_depth": 2,
        "retries": 1,
        "gate_timeout_s": 86400,
        "max_desc_chars": 4000,
    },
    "scheduler": {
        "enabled": True,
        "jobs_file": "state/scheduler.json",
        "misfire_grace_s": 60,
        "coalesce": True,
        "script_timeout_s": 600,
        "jobs": [],
    },
    "memory": {
        "budgets": {
            "memory_chars": 2200,
            "user_chars": 1375,
            "lessons_chars": 1500,
            "index_chars": 1500,
        },
    },
    "discord": {
        "enabled": True,
        "chunk_ms": 700,
        "chunk_chars": 1900,
        "allowlist": {"allow_users": [], "allow_roles": []},
        "home_channel_id": None,  # reconciliation (d): Q4 home channel
    },
    "storage": {"data_dir": "/opt/data/harness"},  # reconciliation (a): /data/harness superseded
}

# Flat env var -> dotted config path bindings for resolution step 4.
# HARNESS_DISCORD_HOME_CHANNEL is handled separately (DISCORD_HOME_CHANNEL fallback).
_FLAT_ENV_BINDINGS: tuple[tuple[str, str], ...] = (
    ("HARNESS_HOST", "server.host"),
    ("HARNESS_PORT", "server.port"),
    ("HARNESS_API_KEY", "api_key"),
    ("HARNESS_DATA_DIR", "storage.data_dir"),
    ("HARNESS_ROUTER_BASE_URL", "router.base_url"),
    ("HARNESS_ROUTER_MODEL", "router.model"),
    ("HARNESS_WORKDIR", "agents.opencode.workdir_root"),
)

_HARNESS_DISCORD_HOME_CHANNEL_ENV = "HARNESS_DISCORD_HOME_CHANNEL"
_DISCORD_HOME_CHANNEL_ENV = "DISCORD_HOME_CHANNEL"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base``: nested mappings merge recursively,
    scalars and lists replace wholesale."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            merged[key] = _deep_merge(base_value, value)
        else:
            merged[key] = value
    return merged


def _resolve_env_placeholders(node: Any, path: str = "") -> Any:
    """Recursively resolve ``${NAME}`` placeholders in every string leaf.

    Each placeholder is replaced from ``os.environ``. Fail-fast (plan R1): a
    referenced variable that is unset or empty raises
    :class:`ConfigError` naming the config key path, e.g.
    ``api_key references ${HARNESS_API_KEY} which is unset``.
    """
    if isinstance(node, dict):
        return {
            key: _resolve_env_placeholders(value, f"{path}.{key}" if path else key)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            _resolve_env_placeholders(item, f"{path}[{index}]") for index, item in enumerate(node)
        ]
    if isinstance(node, str):

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None or value == "":
                key_path = path or "<root>"
                raise ConfigError(
                    key_path, f"{key_path} references ${{{var_name}}} which is unset"
                )
            return value

        return _VAR_PATTERN.sub(_replace, node)
    return node


def _read_flat_env_overrides() -> dict[str, Any]:
    """Collect flat ``HARNESS_*`` env overrides as a nested mapping.

    Empty-string values are treated as unset, so e.g. an empty
    ``HARNESS_DISCORD_HOME_CHANNEL`` falls back to ``DISCORD_HOME_CHANNEL``.
    """
    overrides: dict[str, Any] = {}

    def set_path(dotted_path: str, value: Any) -> None:
        parts = dotted_path.split(".")
        node = overrides
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    for env_name, dotted_path in _FLAT_ENV_BINDINGS:
        value = os.environ.get(env_name)
        if value:
            set_path(dotted_path, value)

    home_channel = os.environ.get(_HARNESS_DISCORD_HOME_CHANNEL_ENV) or os.environ.get(
        _DISCORD_HOME_CHANNEL_ENV
    )
    if home_channel:
        set_path("discord.home_channel_id", home_channel)

    return overrides


class _FlatEnvSource(PydanticBaseSettingsSource):
    """pydantic-settings source binding flat ``HARNESS_*`` env vars to nested paths.

    Resolution step 4: nested model fields bind from flat env names (e.g.
    ``HARNESS_HOST`` -> ``server.host``) rather than nested-delimiter names,
    while every other nested value comes from the YAML/defaults merge. The
    source is returned as the first (highest-priority) entry of
    ``HarnessConfig.settings_customise_sources`` so env always overrides YAML.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Not used: __call__ supplies the whole nested mapping in one shot.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return _read_flat_env_overrides()


class ServerConfig(BaseModel):
    """HTTP gateway server bind settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    host: str = "0.0.0.0"
    # Repo reconciliation (c), user decision Q1: gateway port is 8181 everywhere
    # (the plan said 8080; superseded).
    port: int = 8181


class RateLimitConfig(BaseModel):
    """Request rate-limit settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    per_minute: int = 30
    stream_per_minute: int = 600


class RouterConfig(BaseModel):
    """Model-router settings (ARCHITECTURE.md section 4).

    Repo reconciliation (b), user decision Q3: provider is the generic
    ``openai-compatible`` gateway pointed at ollama by default (the design's
    zai row is superseded); ``base_url``/``model`` are env-overridable via
    ``HARNESS_ROUTER_BASE_URL`` / ``HARNESS_ROUTER_MODEL``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    provider: str = "openai-compatible"
    base_url: str = "http://ollama:11434/v1"
    model: str = "glm-5.3-flash:cloud"
    fallback_model: str | None = None
    max_retries: int = 1
    temperature: float = 0
    timeout_s: int = 20
    # Optional router API key; its ${VAR} placeholders resolve like any other
    # string leaf during load_config step 3.
    api_key: str | None = None


class HermesAgentsConfig(BaseModel):
    """Hermes agent runner settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    enabled: bool = True
    bin: str = "/opt/hermes/.venv/bin/hermes"
    default_model: str | None = None
    # Repo reconciliation per plan section 10: chat timeout raised to researcher
    # parity (the design said 600; superseded).
    timeout_s: int = 3600
    max_output_bytes: int = 4_000_000
    # Repo reconciliation per plan section 10: delegation timeout parity between
    # simple and researcher profiles.
    profile_timeouts: dict[str, int] = {"simple": 900, "researcher": 3600}


class OpencodeAgentsConfig(BaseModel):
    """OpenCode agent runner settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    enabled: bool = True
    bin: str = "opencode"
    default_model: str | None = None
    default_agent: str = "build"
    # Repo reconciliation per plan section 10: 120m parity (the design said 900;
    # superseded).
    timeout_s: int = 7200
    workdir_root: str = "/app/projects"  # env override: HARNESS_WORKDIR
    auto_approve: bool = True


class AgentsConfig(BaseModel):
    """Per-runner agent settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    hermes: HermesAgentsConfig
    opencode: OpencodeAgentsConfig


class HandoffConfig(BaseModel):
    """Child-agent handoff settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    enabled: bool = True
    max_depth: int = 3


class TasksConfig(BaseModel):
    """Inbox task-polling settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    inbox: str = "tasks/inbox"  # relative to storage.data_dir
    poll_seconds: float = 2
    max_concurrent: int = 1
    max_depth: int = 2
    retries: int = 1
    gate_timeout_s: int = 86400
    max_desc_chars: int = 4000


class SchedulerConfig(BaseModel):
    """Cron-like scheduler settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    enabled: bool = True
    jobs_file: str = "state/scheduler.json"
    misfire_grace_s: int = 60
    coalesce: bool = True
    script_timeout_s: int = 600
    jobs: list[dict] = []


class MemoryBudgetsConfig(BaseModel):
    """Per-file character budgets for agent memory (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    memory_chars: int = 2200
    user_chars: int = 1375
    lessons_chars: int = 1500
    index_chars: int = 1500


class MemoryConfig(BaseModel):
    """Agent memory settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    budgets: MemoryBudgetsConfig = Field(default_factory=MemoryBudgetsConfig)


class DiscordAllowlist(BaseModel):
    """Discord interaction allowlist (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    allow_users: list[str] = Field(default_factory=list)
    allow_roles: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    """Discord transport settings (ARCHITECTURE.md section 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    enabled: bool = True
    chunk_ms: int = 700
    chunk_chars: int = 1900
    allowlist: DiscordAllowlist = Field(default_factory=DiscordAllowlist)
    # Repo reconciliation (d), user decision Q4: notices with no originating
    # thread go to the home channel; env HARNESS_DISCORD_HOME_CHANNEL (fallback
    # DISCORD_HOME_CHANNEL).
    home_channel_id: str | None = None


class StorageConfig(BaseModel):
    """On-disk storage settings (ARCHITECTURE.md section 4).

    Repo reconciliation (a): the default ``data_dir`` is ``/opt/data/harness``
    (the design said ``/data/harness``; superseded); env override
    ``HARNESS_DATA_DIR``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    data_dir: str = "/opt/data/harness"


class HarnessConfig(BaseSettings):
    """Top-level harness configuration (ARCHITECTURE.md section 4).

    The only BaseSettings model in the tree: it exists so a nested-settings
    source (:class:`_FlatEnvSource`) can bind flat ``HARNESS_*`` env vars onto
    nested fields (resolution step 4 in the module docstring).

    ``api_key`` has NO usable default: with no YAML and no ``HARNESS_API_KEY``
    env, :func:`load_config` must fail — a missing key surfaces as a pydantic
    missing-field error which is translated to ``ConfigError`` naming
    ``HARNESS_API_KEY``, and an explicitly empty key (e.g. ``api_key: ""`` in
    YAML) is rejected by the belt-and-braces ``_require_api_key`` validator.
    """

    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        extra="forbid",
        validate_assignment=False,
    )

    server: ServerConfig
    api_key: str
    rate_limit: RateLimitConfig
    router: RouterConfig
    agents: AgentsConfig
    handoff: HandoffConfig
    tasks: TasksConfig
    scheduler: SchedulerConfig
    memory: MemoryConfig
    discord: DiscordConfig
    storage: StorageConfig

    @model_validator(mode="after")
    def _require_api_key(self) -> HarnessConfig:
        """Fail fast when api_key is empty after all resolution (plan R1)."""
        if not (self.api_key or "").strip():
            raise ConfigError("api_key", _API_KEY_EMPTY_MESSAGE)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Flat ``HARNESS_*`` env overrides win over the YAML/defaults init kwargs."""
        return (_FlatEnvSource(settings_cls), init_settings)


def _yaml_error_details(exc: yaml.YAMLError) -> str:
    """Extract a readable message (with line/column when available) from a YAML error."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        detail = exc.problem or exc.context or str(exc)
        return f"{detail} (line {mark.line + 1}, column {mark.column + 1})"
    return str(exc)


def _load_yaml_mapping(path: str) -> dict[str, Any]:
    """Read and parse the YAML config file; raise :class:`ConfigLoadError` on problems."""
    if not os.path.isfile(path):
        raise ConfigLoadError(path, f"YAML config file '{path}' does not exist")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigLoadError(
            path, f"invalid YAML in config file '{path}': {_yaml_error_details(exc)}"
        ) from exc
    except OSError as exc:
        raise ConfigLoadError(path, f"could not read YAML config file '{path}': {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigLoadError(
            path,
            f"YAML config file '{path}' must contain a top-level mapping, got {type(raw).__name__}",
        )
    return raw


def _wrap_validation_error(exc: ValidationError) -> ConfigError:
    """Translate a pydantic ValidationError into a readable ConfigError naming the keys."""
    problems: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        err_type = str(err.get("type", ""))
        msg = str(err.get("msg", ""))
        # api_key missing entirely -> the canonical empty-HARNESS_API_KEY error.
        if err_type == "missing" and loc == "api_key":
            return ConfigError("api_key", _API_KEY_EMPTY_MESSAGE)
        # Belt-and-braces: ConfigError raised by the api_key model_validator only
        # propagates unwrapped if pydantic does not wrap it; detect the wrapped
        # form too.
        if err_type == "value_error" and "HARNESS_API_KEY" in msg and "empty" in msg:
            return ConfigError("api_key", msg.removeprefix("Value error, "))
        if err_type == "extra_forbidden":
            where = loc or "<top-level>"
            problems.append(f"unknown config key '{where}' is not allowed")
        elif err_type == "missing":
            problems.append(f"missing required config key '{loc}'")
        elif loc:
            problems.append(f"invalid value for '{loc}': {msg}")
        else:
            problems.append(f"invalid config value: {msg}")
    return ConfigValidationError("config", "invalid harness config: " + "; ".join(problems))


def load_config(path: str | None = None) -> HarnessConfig:
    """Load, merge, resolve, override and validate the harness configuration.

    Args:
        path: explicit YAML config file path; when omitted the loader falls
            back to ``$HARNESS_CONFIG``, then ``./config.yaml``, then just the
            built-in defaults (see resolution step 1 in the module docstring).

    Returns:
        A fully validated :class:`HarnessConfig`.

    Raises:
        ConfigLoadError: the configured YAML file is missing, unreadable, or
            invalid YAML (names the path and the YAML error line/col when
            available).
        ConfigError: a ``${VAR}`` reference is unset (step 3, fail-fast) or
            ``api_key`` is empty/missing after all resolution (plan R1).
        ConfigValidationError: merged values failed model validation
            (also a ``ValueError``), e.g. unknown keys under ``extra="forbid"``.
    """
    config_path = path or os.environ.get(_HARNESS_CONFIG_ENV) or _DEFAULT_CONFIG_FILE
    if path is not None or os.environ.get(_HARNESS_CONFIG_ENV) or os.path.isfile(config_path):
        yaml_values = _load_yaml_mapping(config_path)
    else:
        # No $HARNESS_CONFIG and no ./config.yaml -> built-in defaults, no file needed.
        yaml_values = {}

    # Step 2: deep-merge the YAML onto the built-in defaults.
    merged = _deep_merge(_BUILTIN_DEFAULTS, yaml_values)
    # Step 3: ${VAR} resolution (after merge, before env overrides / validation).
    resolved = _resolve_env_placeholders(merged)

    try:
        # Steps 4 + 5: flat HARNESS_* env overrides (pydantic-settings source) then
        # model validation with extra="forbid".
        return HarnessConfig(**resolved)
    except ConfigError:
        raise
    except ValidationError as exc:
        raise _wrap_validation_error(exc) from exc


def effective_router_settings(cfg: HarnessConfig) -> dict[str, Any]:
    """Router connection settings for OpenAI-compatible clients (ARCHITECTURE.md section 4).

    Returns ``{"base_url": ..., "model": ..., "api_key": ...}``. ``api_key`` is
    the optional ``router.api_key`` (its ``${VAR}`` placeholders were resolved
    during :func:`load_config`) or ``None`` when unset.
    """
    return {
        "base_url": cfg.router.base_url,
        "model": cfg.router.model,
        "api_key": cfg.router.api_key,
    }
