"""F4/F5 route()-level provider-down: model raises x2 -> keyword fallback + honest ledger rows (R12a gap-fill).

The schema-invalid path (``tests/test_router.py``) retries twice
(``cfg.router.max_retries == 1``) and then falls back. This file pins the
provider-DOWN variant: the injected model raises instead of returning an
invalid response.

Observed real behavior (do not "fix" here):

* A provider/API error (``ModelAPIError``, the type the real OpenAIChatModel
  seam raises for a down provider) is swallowed by ``route()`` after the FIRST
  raise — model errors are not schema retries, so the second scripted raise is
  never requested (``model.calls`` stays at 1). Fallback fires immediately and
  the ledger gets exactly two rows: one failed ``kind=route`` attempt row
  (``agent=None``, ``error="ModelAPIError: ..."``) plus one ``kind=fallback``
  decision row (``agent=<keyword>``, no ``error`` field).
* A bare ``RuntimeError`` raised from the model is NOT in router
  ``_CALL_ERRORS`` and therefore escapes ``route()`` unledgered. That is a
  separate, documented divergence: the real provider seam does not surface
  RuntimeError, but the model-crash boundary is not covered by the fallback.

Zero network: a ``FunctionModel`` object is injected through the R-3 seam.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.function import FunctionModel

from harness.config import HarnessConfig
from harness.persist import read_jsonl
from harness.router import Route, RouterBrain
from harness.session import Session

_MODEL_NAME = "provider-down-router"
_PROVIDER_DOWN = "provider down"


def make_provider_down_model(
    *, raise_times: int = 2, model_name: str = _MODEL_NAME
) -> FunctionModel:
    """Scripted model: raises ``ModelAPIError`` for the first ``raise_times``
    calls, then returns a valid route (never reached via route() fallback)."""
    calls: list[list[Any]] = []
    valid = json.dumps({"agent": "hermes", "task_title": "unused valid response"})

    def _function(messages: list[Any], info: Any) -> ModelResponse:
        calls.append(list(messages))
        if len(calls) <= raise_times:
            raise ModelAPIError(model_name, _PROVIDER_DOWN)
        return ModelResponse(parts=[TextPart(content=valid)])

    model = FunctionModel(_function, model_name=model_name)
    model.calls = calls  # type: ignore[attr-defined] — same seam as tests/mocks/router_model.py
    return model


def _session(tmp_volume: Path, cfg: HarnessConfig, session_id: str = "ABCDEFGH") -> Session:
    return Session.create(tmp_volume / "sessions" / session_id, cfg, session_id=session_id)


def test_route_provider_down_swallowed_keyword_fallback_and_ledger(cfg) -> None:
    model = make_provider_down_model()
    brain = RouterBrain(cfg, model=model)

    route = brain.route("please implement the refactor of the auth module")

    assert isinstance(route, Route)
    assert route.agent == "opencode"  # keyword table: "implement" -> opencode
    assert route.task_title == "please implement the refactor of the auth module"
    assert route.need_context is False
    assert route.skill_hints == []

    # Provider errors are not schema retries: only the first raise is requested.
    assert len(model.calls) == 1

    rows = brain.ledger_rows()
    assert len(rows) == 2
    failed, decision = rows

    assert failed["kind"] == "route"
    assert failed["fallback"] is True
    assert failed["agent"] is None
    assert failed["error"] == "ModelAPIError: provider down"
    assert failed["confidence"] is None
    assert failed["model"] == _MODEL_NAME
    assert failed["session_id"] is None
    assert failed["ts"].endswith("Z")
    assert isinstance(failed["latency_ms"], int)

    assert decision["kind"] == "fallback"
    assert decision["fallback"] is True
    assert decision["agent"] == route.agent
    assert "error" not in decision
    assert decision["model"] == _MODEL_NAME
    assert decision["session_id"] is None


async def test_route_async_provider_down_banner(cfg, tmp_volume) -> None:
    brain = RouterBrain(cfg, model=make_provider_down_model())
    session = _session(tmp_volume, cfg)

    route = await brain.route_async("fix the login bug", session=session)

    assert route.agent == "opencode"  # keyword table: "fix" -> opencode
    lines = read_jsonl(session.transcript_path)
    system_lines = [line for line in lines if line.get("kind") == "system"]
    assert len(system_lines) == 1
    assert system_lines[0]["data"] == {"note": "routed by fallback (keyword table)"}

    rows = brain.ledger_rows()
    assert [row["kind"] for row in rows] == ["route", "fallback"]
    assert rows[0]["session_id"] == session.id
    assert rows[0]["error"] == "ModelAPIError: provider down"
    assert rows[1]["session_id"] == session.id
    assert rows[1]["agent"] == "opencode"


def test_route_bare_runtime_error_escapes_unledgered(cfg) -> None:
    """Divergence finding: a non-provider exception bypasses the F4/F5 net."""
    calls: list[list[Any]] = []

    def _boom(messages: list[Any], info: Any) -> ModelResponse:
        calls.append(list(messages))
        raise RuntimeError(_PROVIDER_DOWN)

    brain = RouterBrain(cfg, model=FunctionModel(_boom, model_name="boom-router"))

    with pytest.raises(RuntimeError, match=_PROVIDER_DOWN):
        brain.route("please implement the refactor of the auth module")

    assert len(calls) == 1
    assert brain.ledger_rows() == []
