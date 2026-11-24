"""Shared pytest fixtures for the bob-harness test suite."""

from __future__ import annotations

import importlib
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from bob_harness.config import HarnessConfig, load_config

_CONFIG_ENV_VARS = (
    "BOB_HARNESS_CONFIG",
    "BOB_HARNESS_API_KEY",
    "BOB_HARNESS_HOST",
    "BOB_HARNESS_PORT",
    "BOB_HARNESS_DATA_DIR",
    "BOB_HARNESS_ROUTER_BASE_URL",
    "BOB_HARNESS_ROUTER_MODEL",
    "BOB_HARNESS_WORKDIR",
    "BOB_HARNESS_ROUTER_API_KEY",
    "BOB_HARNESS_DISCORD_HOME_CHANNEL",
    "DISCORD_HOME_CHANNEL",
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_ROLES",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def tmp_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    volume = tmp_path / "bob-harness"
    monkeypatch.setenv("BOB_HARNESS_DATA_DIR", str(volume))
    monkeypatch.setenv("BOB_HARNESS_API_KEY", "test-key-123")
    return volume


@pytest.fixture
def cfg(tmp_volume: Path) -> HarnessConfig:
    return load_config()


@pytest.fixture
def supervisor_module() -> types.ModuleType:
    return importlib.import_module("bob_harness.supervisor")
