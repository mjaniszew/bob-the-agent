"""P1 v2-port tests (plan SS4 P1) — pydantic-ai 2.73.0 surfaces.

Same zero-network style as tests/test_router.py: a fake model OBJECT injected
via ``RouterBrain(cfg, model=make_fake_model([...]))`` (tests/mocks/router_model.py,
``cfg`` fixture from conftest). The test numbers follow the P1 section of
``docs/plans/10-pydantic-ai-v2-migration-plan.md``. This is step 1
(failing-first TDD) of the router v2 port: pins the 2.73.0 API surface the
port must keep — capability-free ctor kwargs (D7), run.response/output/
usage/all_messages, the ModelSettings dict shape, the v2 _CALL_ERRORS
imports, retry-note acceptance of v2 part shapes, the ledger semantics, and
the budget constants. While ``_retry_notes`` still only speaks v1
``RetryPromptPart`` shapes, the ledger-semantics test (7) is THE red test.
"""

from __future__ import annotations

import functools
import json
import types
import warnings

import pytest
from pydantic_ai import ModelResponse
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import RetryFeedbackPart, RetryInfo, RetryPromptPart, UserPromptPart
from pydantic_ai.usage import RunUsage

import bob_harness.router as router_mod
from bob_harness.router import _CALL_ERRORS, Route, RouterBrain, _retry_notes
from tests.mocks.router_model import make_fake_model, sample

# The verified-live 2.73.0 model-facing retry wire: the validation-retry round
# renders as a user-voice fenced prompt (TASK B pin re-used here verbatim).
_FRAMED = (
    "Your previous response failed the application's validation (this is automated feedback,"
    " not a message from the user):\n<pyai:validation_errors>\n"
    '[{"type":"json_invalid","loc":[],"msg":"Invalid JSON: bad","input":"{x"}]\n'
    "</pyai:validation_errors>"
)

# The shared v2-shaped validator details of every synthetic retry part below.
_DETAILS = [{"type": "json_invalid", "loc": [], "msg": "Invalid JSON: bad", "input": "{x"}]


def _one_part_msg(part: object) -> types.SimpleNamespace:
    """A synthetic history message carrying exactly one part."""
    return types.SimpleNamespace(parts=[part])


# ---------------------------------------------------------------------------
# 1: D7 — both lazy agents constructed capability-free (v2 ctor kwargs shape)
# ---------------------------------------------------------------------------


def test_router_ctor_args_v2_shape(cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_fake_model([sample("route_valid")])
    recorded: list[dict] = []
    original_agent = router_mod.Agent

    @functools.wraps(original_agent)
    def _recording_agent(*args: object, **kwargs: object) -> object:
        recorded.append(kwargs)
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(router_mod, "Agent", _recording_agent)
    brain = RouterBrain(cfg, model=fake)
    route_agent = brain._agent_route  # first lazy construction happens HERE
    analyze_agent = brain._agent_analyze  # second lazy construction HERE
    assert len(recorded) == 2
    assert [kwargs["name"] for kwargs in recorded] == ["route_agent", "analyze_agent"]
    assert [kwargs["output_type"] for kwargs in recorded] == [Route, router_mod.Analysis]
    for kwargs in recorded:
        assert set(kwargs) == {"model", "name", "output_type", "model_settings", "retries"}
        # D7: pydantic-ai 2.73.0 Agent has NO .capabilities instance attribute (it is
        # only an __init__ param; auto-injected infra capabilities live under
        # .root_capability) — a "capabilities" kwarg would be wrong in both directions.
        assert "capabilities" not in kwargs
        assert kwargs["model"] is fake
        assert kwargs["retries"] == max(0, int(cfg.router.max_retries))
    assert route_agent is brain._route_agent and analyze_agent is brain._analyze_agent


# ---------------------------------------------------------------------------
# 2/3: the v2 run() result surface (response/output/usage/all_messages)
# ---------------------------------------------------------------------------


def test_run_result_response_and_output(cfg) -> None:
    brain = RouterBrain(cfg, model=make_fake_model([sample("route_valid")]))
    brain.route("hello")
    run2 = brain._agent_route.run_sync(
        "one-route ping: reply with the JSON route object (agent of your choice)."
    )
    assert isinstance(run2.output, Route)  # 2.73.0: validated output on the run itself
    assert isinstance(run2.response, ModelResponse)  # 2.73.0: .response alongside .output


def test_run_usage_requests_and_all_messages(cfg) -> None:
    brain = RouterBrain(cfg, model=make_fake_model([sample("route_valid")]))
    run = brain._agent_route.run_sync(
        "one-route ping: reply with the JSON route object (agent of your choice)."
    )
    # 2.73.0: run.usage is the run-level RunUsage PROPERTY (do NOT call it); its
    # ``.requests`` feeds router._split_latency. (RequestUsage is the per-request
    # view; the run-level aggregate on 2.73.0 is RunUsage.)
    assert isinstance(run.usage, RunUsage)
    assert run.usage.requests >= 1
    assert isinstance(run.all_messages(), list)
    assert len(run.all_messages()) >= 2


# ---------------------------------------------------------------------------
# 4: model settings shape (temperature 0 preserved across the port)
# ---------------------------------------------------------------------------


def test_model_settings_temperature_zero(cfg) -> None:
    brain = RouterBrain(cfg, model=make_fake_model([sample("route_valid")]))
    # Plan line 315 said isinstance(x, ModelSettings), but on 2.73.0 ModelSettings
    # is a TypedDict (typing_extensions._TypedDictMeta) — isinstance is False by
    # definition. The dict-shape pin preserves the intent: temperature=0 survives.
    assert dict(brain._model_settings) == {"temperature": 0}
    assert brain._model_settings.get("temperature") == 0


# ---------------------------------------------------------------------------
# 5: the v2 exception surface of _CALL_ERRORS
# ---------------------------------------------------------------------------


def test_call_errors_tuple_v2_imports() -> None:
    assert UnexpectedModelBehavior in _CALL_ERRORS
    assert ModelHTTPError in _CALL_ERRORS
    assert ModelAPIError in _CALL_ERRORS
    assert all(issubclass(error, BaseException) for error in _CALL_ERRORS)


# ---------------------------------------------------------------------------
# 6: _retry_notes must accept the v2 part shapes (legacy pins kept green)
# ---------------------------------------------------------------------------


def test_retry_notes_accept_v2_content_list() -> None:
    details: list[dict] = [dict(item) for item in _DETAILS]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 2.73.0 deprecation on legacy instantiation
        legacy_str_part = RetryPromptPart(content="json_invalid: boom")
        legacy_list_part = RetryPromptPart(content=details)
    v2_feedback = RetryFeedbackPart(content=details, retry=RetryInfo(cause="validation_error"))
    v2_wire = UserPromptPart(content=_FRAMED)
    legacy_str_note = json.dumps("json_invalid: boom")[:120]
    v2_raw_note = json.dumps(details, default=str)[:120]
    wire_note = _FRAMED[:120]
    # a: legacy str part — the CURRENT handler, kept as a green pin.
    assert _retry_notes([_one_part_msg(legacy_str_part)]) == [legacy_str_note]
    # b: legacy list part — already handled today, kept as a green pin.
    assert _retry_notes([_one_part_msg(legacy_list_part)]) == [v2_raw_note]
    # c: v2 raw RetryFeedbackPart (agent-side history shape).
    assert _retry_notes([_one_part_msg(v2_feedback)]) == [v2_raw_note]
    # d: v2 model-facing wire — UserPromptPart with the fenced validation echo.
    assert _retry_notes([_one_part_msg(v2_wire)]) == [wire_note]
    # e: negative + order — plain prompts yield nothing; mixed shapes stay in order.
    plain = _one_part_msg(UserPromptPart(content="just a normal user prompt"))
    assert _retry_notes([plain]) == []
    ordered = _retry_notes([
        _one_part_msg(legacy_str_part),
        _one_part_msg(v2_feedback),
        _one_part_msg(v2_wire),
    ])
    assert ordered == [legacy_str_note, v2_raw_note, wire_note]


# ---------------------------------------------------------------------------
# 7: THE red test (TDD) — ledger semantics on the v2 wire through route_async
# ---------------------------------------------------------------------------


async def test_retry_notes_wire_shape_end_to_end(cfg) -> None:
    """One invalid-then-valid route_async: the FIRST attempt row must carry a
    real v2-extracted validator note (not the "output validation retry"
    placeholder) while ``_retry_notes`` still ignores the v2 part shapes."""
    brain = RouterBrain(
        cfg, model=make_fake_model([sample("route_invalid_1"), sample("route_valid")])
    )
    route = await brain.route_async("implement the refactor cleanly")
    assert route.agent == "opencode"
    rows = brain.ledger_rows()
    first = next(row for row in rows if row["kind"] == "route" and row["fallback"] is True)
    assert "error" in first and first["error"] is not None
    assert first["error"] != "output validation retry"  # v2 extraction must produce a REAL note
    assert "json_invalid" in first["error"]
    assert len(first["error"]) <= 120


# ---------------------------------------------------------------------------
# 8: budget constants unchanged by the port (guards TEST_PLAN row 11)
# ---------------------------------------------------------------------------


def test_budget_constants_unchanged() -> None:
    assert router_mod._PROMPT_CHAR_CAP == 9000
    assert router_mod._MAX_DIGEST_CHARS == 600
    assert router_mod._SKILL_INDEX_CAP == 1500
    assert router_mod._SHORT_REASON_CAP == 120
    assert router_mod._LAST_EVENTS == 3
    assert "workdir MUST start with" in router_mod._ANALYZE_SCHEMA
    assert router_mod._FALLBACK_KEYWORDS == {
        "opencode": ("code", "implement", "refactor", "bug", "fix"),
        "hermes": ("research", "summarize", "deploy", "chat"),
    }
