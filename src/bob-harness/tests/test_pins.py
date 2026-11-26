"""Failing-first P0 battery pinning the pydantic-ai v2 dependency contract (docs/plans/10, §P0)."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = Path(__file__).resolve().parents[1]

_LOCK_PINS = (
    "pydantic-ai==2.73.0",
    "pydantic-ai-slim==2.73.0",
    "pydantic-graph==2.73.0",
    "pydantic-evals==2.73.0",
    "pydantic-ai-harness==0.73.0",
)

_COMPOSE_BANNER_RE = re.compile(
    r"^(?:-\s*)?['\"]?PYDANTIC_AI_NO_BANNER['\"]?\s*[:=]\s*['\"]?1['\"]?\s*(?:#.*)?$"
)
_CONFTEST_GUARD_RE = re.compile(
    r"os\.environ(?:\.setdefault\(\s*|\[\s*)['\"]PYDANTIC_AI_NO_BANNER['\"]"
    r"\s*(?:,\s*['\"]1['\"]\s*\)|\]\s*=\s*['\"]1['\"])"
)


def _compose_agent_env_block(compose_text: str) -> list[str]:
    lines = compose_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^  agent:\s*$", ln)), None)
    assert start is not None, "compose.yaml: no top-level 'agent' service"
    sibling = re.compile(r"^  [A-Za-z][A-Za-z0-9_-]*:\s*(?:#.*)?$")
    end = next((i for i in range(start + 1, len(lines)) if sibling.match(lines[i])), len(lines))
    service = lines[start + 1 : end]
    env_idx = next((i for i, ln in enumerate(service) if ln.strip() == "environment:"), None)
    assert env_idx is not None, "compose.yaml agent service: no environment block"
    out: list[str] = []
    for ln in service[env_idx + 1 :]:
        if not ln.strip():
            continue
        if len(ln) - len(ln.lstrip(" ")) <= 4:
            break
        out.append(ln)
    return out


def test_pydantic_ai_major_is_2() -> None:
    version = importlib.metadata.version("pydantic-ai")
    assert version.startswith("2."), version


def test_harness_pinned_0730() -> None:
    assert importlib.metadata.version("pydantic-ai-harness") == "0.73.0"


def test_coder_extra_importable() -> None:
    importlib.import_module("pydantic_ai_harness.coder")
    importlib.import_module("json_repair")


def test_requirements_txt_cap_removed() -> None:
    text = (_HARNESS_DIR / "requirements.txt").read_text(encoding="utf-8")
    lines = text.splitlines()
    pins = [raw.split("#", 1)[0].strip() for raw in lines]

    assert "pydantic-ai>=1,<2" not in text, lines
    assert any(pin.startswith("pydantic-ai>=2.73,<3") for pin in pins), pins
    assert any(pin == "pydantic-ai-harness[coder]==0.73.0" for pin in pins), pins
    assert any("superseded by" in raw for raw in lines), lines


def test_requirements_lock_v2_lines() -> None:
    text = (_HARNESS_DIR / "requirements.lock").read_text(encoding="utf-8")
    pins = {raw.split("#", 1)[0].strip() for raw in text.splitlines()}
    for pin in _LOCK_PINS:
        assert pin in pins
    json_repair = [pin for pin in pins if pin.startswith("json-repair==")]
    assert len(json_repair) == 1, json_repair


def test_banner_env_set() -> None:
    dockerfile = (_REPO_ROOT / "dockerfiles" / "Dockerfile.hermes").read_text(encoding="utf-8")
    assert any(
        raw.strip().startswith("ENV") and "PYDANTIC_AI_NO_BANNER=1" in raw
        for raw in dockerfile.splitlines()
    ), "Dockerfile.hermes: no ENV PYDANTIC_AI_NO_BANNER=1"
    compose = (_REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    block = _compose_agent_env_block(compose)
    assert any(_COMPOSE_BANNER_RE.match(raw.strip()) for raw in block), block


def test_conftest_sets_no_banner() -> None:
    lines = (_HARNESS_DIR / "tests" / "conftest.py").read_text(encoding="utf-8").splitlines()
    guard = next((i for i, raw in enumerate(lines) if _CONFTEST_GUARD_RE.search(raw)), None)
    assert guard is not None, (
        "conftest.py: no PYDANTIC_AI_NO_BANNER env guard before any pydantic_ai reference"
    )
    first_pydantic_ai = next((i for i, raw in enumerate(lines) if "pydantic_ai" in raw), None)
    if first_pydantic_ai is not None:
        assert guard < first_pydantic_ai, (guard, first_pydantic_ai)


def test_logfire_instrument_pydantic_ai_callable_under_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logfire

    assert hasattr(logfire, "instrument_pydantic_ai")
    monkeypatch.setenv("LOGFIRE_IGNORE_NO_CONFIG", "1")
    logfire.configure(
        send_to_logfire=False,
        environment="local",
        service_name="bob-harness-test",
        console=False,
    )
    logfire.instrument_pydantic_ai()
