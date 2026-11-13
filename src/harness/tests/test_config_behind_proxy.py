"""M-5b ``server.behind_proxy`` tests (R6 orchestrator correction 2: schema
field + flat env binding + the supervisor uvicorn wiring)."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import uvicorn
from fastapi import FastAPI

from harness.config import ServerConfig, load_config


def test_server_config_field_default_false() -> None:
    config = ServerConfig()
    assert config.behind_proxy is False
    assert config.host == "0.0.0.0"
    assert config.port == 8181  # user decision Q1 — NEVER 8080


def test_server_config_field_accepts_true() -> None:
    assert ServerConfig(behind_proxy=True).behind_proxy is True


def test_env_override_true(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.setenv("HARNESS_BEHIND_PROXY", "1")
    config = load_config()
    assert config.server.behind_proxy is True


def test_env_override_true_words(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.setenv("HARNESS_BEHIND_PROXY", "yes")
    assert load_config().server.behind_proxy is True


def test_env_override_false_word(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.setenv("HARNESS_BEHIND_PROXY", "false")
    assert load_config().server.behind_proxy is False


def test_env_default_false(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.delenv("HARNESS_BEHIND_PROXY", raising=False)
    assert load_config().server.behind_proxy is False


class _FakeConfig:
    """Captures the raw uvicorn.Config kwargs passed by supervisor._serve()."""

    captured: ClassVar[dict] = {}

    def __init__(self, app: FastAPI, **kwargs) -> None:
        type(self).captured = {"app": app, **kwargs}


class _FakeServer:
    def __init__(self, config: object) -> None:
        self.config = config

    async def serve(self) -> None:
        return None


def test_serve_passes_proxy_headers_when_behind_proxy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.setenv("HARNESS_BEHIND_PROXY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "stub-key")
    from harness.supervisor import _serve

    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    assert asyncio.run(_serve(load_config())) == 0
    captured = _FakeConfig.captured
    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "127.0.0.1"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8181


def test_serve_leaves_uvicorn_default_without_behind_proxy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "harness"))
    monkeypatch.setenv("HARNESS_API_KEY", "test-key-123")
    monkeypatch.delenv("HARNESS_BEHIND_PROXY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "stub-key")
    from harness.supervisor import _serve

    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    assert asyncio.run(_serve(load_config())) == 0
    captured = _FakeConfig.captured
    assert "proxy_headers" not in captured
    assert "forwarded_allow_ips" not in captured


def test_build_app_zero_arg_stub_unchanged(supervisor_module) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(supervisor_module.build_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
