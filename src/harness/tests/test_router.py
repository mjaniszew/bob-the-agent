"""Tests for harness.router (TEST_PLAN row 11 + D16 analyze, plan R5).

Zero-network: every test injects a fake model OBJECT into
``RouterBrain(cfg, model=fake)`` (the R-3 mock seam) — no httpx/transport
patching, no env fiddling beyond the conftest fixtures.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models.function import FunctionModel

from harness.bus import Bus
from harness.persist import read_jsonl
from harness.router import (
    Analysis,
    Route,
    RouterBrain,
    long_session_guard,
    validate_handoff_block,
)
from harness.session import Session
from tests.mocks.router_model import make_fake_model, sample

_PROMPT_CAP_MARGIN = 2200 * 4 + 250


def _session(tmp_volume, cfg, session_id: str = "ABCDEFGH") -> Session:
    return Session.create(
        tmp_volume / "sessions" / session_id, cfg, session_id=session_id
    )


def _brain(cfg, outputs, bus=None) -> tuple[RouterBrain, FunctionModel]:
    model = make_fake_model(outputs)
    return RouterBrain(cfg, bus=bus, model=model), model


# ---------------------------------------------------------------------------
# 1: schema matrix (mocked model object)
# ---------------------------------------------------------------------------


def test_route_valid_roundtrip(cfg) -> None:
    brain, model = _brain(cfg, [sample("route_valid")])
    route = brain.route("please fix the login bug in the auth module")
    assert isinstance(route, Route)
    assert route.agent == "opencode"
    assert route.task_title == "Fix login bug"
    assert route.need_context is False
    assert route.skill_hints == []
    assert model.calls[0][0].parts[0].content.startswith("You are the router")
    assert len(brain.ledger_rows()) == 1


async def test_route_one_invalid_then_valid_retries(cfg) -> None:
    brain, model = _brain(
        cfg, [sample("route_invalid_1"), sample("route_valid")], bus=Bus()
    )
    route = await brain.route_async("implement the refactor cleanly")
    assert route.agent == "opencode"
    assert len(model.calls) == 2
    retry = [part for message in model.calls[1] for part in message.parts]
    assert isinstance(retry[-1], RetryPromptPart)
    assert "json_invalid" in str(retry[-1].content) or isinstance(retry[-1].content, list)
    rows = brain.ledger_rows()
    assert rows[-1]["kind"] == "route" and rows[-1]["fallback"] is False


def test_route_invalid_twice_swallowed_keyword_fallback(cfg) -> None:
    brain, model = _brain(
        cfg,
        [sample("route_invalid_1"), sample("route_invalid_2"), sample("route_valid")],
    )
    route = brain.route("please implement the refactor of the auth module")
    assert len(model.calls) == 2
    assert route.agent == "opencode"  # keyword table: "implement" -> opencode
    rows = brain.ledger_rows()
    assert sum(1 for row in rows if row["kind"] == "fallback") == 1
    assert sum(1 for row in rows if row["kind"] == "route" and row["fallback"]) == 2
    fallback_row = next(row for row in rows if row["kind"] == "fallback")
    assert fallback_row["agent"] == route.agent


# ---------------------------------------------------------------------------
# 2: fallback truth table (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("fix the login bug", "opencode"),
        ("implement X", "opencode"),
        ("code review", "opencode"),
        ("refactor", "opencode"),
        ("research X", "hermes"),
        ("summarize", "hermes"),
        ("deploy flask", "hermes"),
        ("hello", "hermes"),
        ("FIX now", "opencode"),
    ],
)
def test_keyword_fallback_table(text: str, expected: str) -> None:
    from harness.router import _keyword_fallback

    assert _keyword_fallback(text) == expected


# ---------------------------------------------------------------------------
# 3: prompt-cap proxy (len proxy per TEST_PLAN row 11)
# ---------------------------------------------------------------------------


def _seed_big_memory(tmp_volume) -> None:
    bullets = "\n".join(f"- memory fact line {index} " + "x" * 80 for index in range(40))
    user = "\n".join(f"- user preference line {index} " + "x" * 80 for index in range(40))
    (tmp_volume / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_volume / "memory" / "MEMORY.md").write_text("## Memory\n" + bullets + "\n")
    (tmp_volume / "memory" / "USER.md").write_text("## User\n" + user + "\n")


def test_prompt_cap_keeps_input(cfg, tmp_volume) -> None:
    _seed_big_memory(tmp_volume)
    brain, _model = _brain(cfg, [sample("route_valid")])
    text = "long but fitting user turn\n" * 130
    prompt = brain._build_route_prompt(text, need_skill_index=False)
    assert len(prompt) <= _PROMPT_CAP_MARGIN
    assert prompt.endswith("## Input\n" + text.strip())


def test_prompt_cap_truncates_flooded_input(cfg, tmp_volume) -> None:
    brain, _model = _brain(cfg, [sample("route_valid")])
    text = "flood " * 6000
    prompt = brain._build_route_prompt(text, need_skill_index=False)
    assert len(prompt) <= 9000 < _PROMPT_CAP_MARGIN
    assert text[:300] in prompt
    assert prompt.endswith("…[input truncated]")


def test_prompt_includes_memory_events_and_input(cfg, tmp_volume) -> None:
    _seed_big_memory(tmp_volume)
    session = _session(tmp_volume, cfg)

    async def _seed() -> None:
        await session.append_event("user", {"text": "earlier user turn"})
        await session.append_event("router", {"agent": "opencode"})

    asyncio.run(_seed())
    brain, _model = _brain(cfg, [sample("route_valid")])
    prompt = brain._build_route_prompt("fix the bug", session=session, need_skill_index=False)
    assert "## Memory" in prompt and "MEMORY:" in prompt and "USER:" in prompt
    assert "## Recent session events" in prompt
    assert any(line.startswith("user:") for line in prompt.splitlines())
    assert prompt.endswith("## Input\nfix the bug")


# ---------------------------------------------------------------------------
# 4: ledger row shapes
# ---------------------------------------------------------------------------


def test_ledger_rows_route_then_fallback(cfg) -> None:
    good, _model = _brain(cfg, [sample("route_valid")])
    good.route("fix the bug please")
    rows = good.ledger_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "route"
    assert row["agent"] == "opencode"
    assert row["fallback"] is False
    assert row["model"] == "fake-router"
    assert isinstance(row["latency_ms"], int)
    assert row["ts"].endswith("Z")
    assert row["session_id"] is None

    bad, model = _brain(
        cfg,
        [sample("route_invalid_1"), sample("route_invalid_2"), sample("fallback_route")],
    )
    bad.route("research the options for deploy")
    extra = [row for row in bad.ledger_rows() if row["kind"] == "fallback"]
    assert len(extra) == 1
    assert extra[0]["agent"] == "hermes"  # keyword table: "research" -> hermes
    assert len(model.calls) == 2


# ---------------------------------------------------------------------------
# 5: analyze() flavor (D16)
# ---------------------------------------------------------------------------


def test_analyze_valid_full_fields(cfg) -> None:
    brain, _model = _brain(cfg, [sample("analyze_valid")])
    analysis = brain.analyze("run the failing pytest suite for the site project")
    assert isinstance(analysis, Analysis)
    assert analysis.agent == "opencode"
    assert analysis.mode == "gate"
    assert analysis.workdir == "/app/projects/site"
    assert analysis.prompt_seed == "Fix the failing pytest"
    assert analysis.task_title == "Fix pytest"
    assert analysis.confidence == 0.8
    assert analysis.need_context is False
    assert analysis.skill_hints == []
    rows = brain.ledger_rows()
    assert [row["kind"] for row in rows] == ["analyze"]
    assert rows[0]["fallback"] is False and rows[0]["confidence"] == 0.8


def test_analyze_invalid_twice_deterministic_fallback(cfg) -> None:
    brain, model = _brain(
        cfg,
        [
            sample("route_invalid_1"),
            sample("route_invalid_2"),
            sample("analyze_valid"),
        ],
    )
    analysis = brain.analyze("do the scheduled cleanup sweep")
    assert analysis.agent == "hermes"
    assert analysis.mode == "oneway"
    assert analysis.workdir == cfg.agents.opencode.workdir_root
    assert len(model.calls) == 2
    rows = brain.ledger_rows()
    assert sum(1 for row in rows if row["kind"] == "analyze") == 2
    assert rows[-1]["kind"] == "analyze_fallback"
    assert rows[-1]["agent"] == "hermes"


# ---------------------------------------------------------------------------
# 6: Analysis workdir validation is model-level (feeds the retry machinery)
# ---------------------------------------------------------------------------


def test_analysis_workdir_rejected_then_fallback(cfg) -> None:
    brain, model = _brain(
        cfg,
        [
            {"agent": "opencode", "mode": "oneway", "workdir": "/etc/passwd",
             "prompt_seed": "s", "task_title": "t"},
        ],
    )
    analysis = brain.analyze("do the task")
    assert analysis.agent == "hermes"
    assert analysis.workdir == cfg.agents.opencode.workdir_root
    assert len(model.calls) == 2


def test_analyze_default_workdir_none_argument(cfg) -> None:
    brain, _model = _brain(cfg, [sample("analyze_valid")])
    analysis = brain.analyze("task with its own workdir", workdir=None)
    assert analysis.workdir == "/app/projects/site"


# ---------------------------------------------------------------------------
# 7: health_check (boot ping, section 3.4)
# ---------------------------------------------------------------------------


def test_health_check_true_false_and_raising(cfg) -> None:
    good, _model = _brain(cfg, [sample("route_valid")])
    assert good.health_check() is True
    assert good.ledger_rows()[-1]["kind"] == "ping"
    assert good.ledger_rows()[-1]["fallback"] is False

    bad, _model = _brain(cfg, ["{not json"])
    assert bad.health_check() is False
    assert bad.ledger_rows()[-1]["kind"] == "ping"
    assert "error" in bad.ledger_rows()[-1]

    def _boom(messages, info):
        raise RuntimeError("provider down")

    raising_model = FunctionModel(_boom, model_name="boomer")
    raising = RouterBrain(cfg, model=raising_model)
    assert raising.health_check() is False
    assert raising.ledger_rows()[-1]["agent"] is None


# ---------------------------------------------------------------------------
# 8: long_session_guard (section 6 compaction payload)
# ---------------------------------------------------------------------------


def test_long_session_guard() -> None:
    assert long_session_guard("short text") is None
    assert long_session_guard("x" * 25_000) is None
    payload = long_session_guard("x" * 25_001)
    assert payload is not None
    assert payload["reset"] is True
    assert payload["reason"] == "final_text 25001 chars exceeds 25000"
    assert len(payload["summary"]) <= 480
    assert payload["summary"] == "x" * 480
    multi = "line1\nline2\nline3\nline4\nline5\nline6\n" + "x" * 25_000
    guarded = long_session_guard(multi)
    assert guarded is not None
    assert guarded["summary"] == "line1 | line2 | line3 | line4"


# ---------------------------------------------------------------------------
# 9: handoff-block helper (section 3.2 chat scope, depth advisory)
# ---------------------------------------------------------------------------


def test_validate_handoff_block() -> None:
    block = "pre\n```HANDOFF\nAGENT: opencode\nPROMPT: do the thing\n```\npost"
    assert validate_handoff_block(block) == {"agent": "opencode", "prompt": "do the thing"}
    extra = "```HANDOFF\nAGENT: hermes\nREASON: why not\nPROMPT: go\n```"
    assert validate_handoff_block(extra) == {"agent": "hermes", "prompt": "go"}
    long_prompt = "```HANDOFF\nAGENT: opencode\nPROMPT: " + "x" * 801 + "\n```"
    assert validate_handoff_block(long_prompt) is None
    assert validate_handoff_block("```HANDOFF\nAGENT: wat\nPROMPT: hi\n```") is None
    assert validate_handoff_block("```HANDOFF\nAGENT: opencode\n```") is None
    assert validate_handoff_block("no block here") is None
    assert validate_handoff_block("```HANDOFF\nAGENT: opencode\nPROMPT: hi\n") is None
    assert validate_handoff_block(
        "```HANDOFF\nAGENT: opencode\nPROMPT: hi\n```", max_depth=3, depth=3
    ) is None


# ---------------------------------------------------------------------------
# 10: fallback banner (m4b) via route_async + sync skip without loop
# ---------------------------------------------------------------------------


async def test_route_async_banner_on_fallback(cfg, tmp_volume) -> None:
    brain, model = _brain(
        cfg,
        [
            sample("route_invalid_1"),
            sample("route_invalid_2"),
            sample("fallback_route"),
        ],
    )
    session = _session(tmp_volume, cfg)
    route = await brain.route_async("please fix the bug in the code", session=session)
    assert route.agent == "opencode"
    assert len(model.calls) == 2
    lines = read_jsonl(session.transcript_path)
    system_lines = [line for line in lines if line.get("kind") == "system"]
    assert len(system_lines) == 1
    assert system_lines[0]["data"] == {"note": "routed by fallback (keyword table)"}


async def test_route_async_clean_no_banner(cfg, tmp_volume) -> None:
    brain, _model = _brain(cfg, [sample("route_valid")])
    session = _session(tmp_volume, cfg)
    route = await brain.route_async("hello there", session=session)
    assert isinstance(route, Route)
    lines = read_jsonl(session.transcript_path)
    assert not [line for line in lines if line.get("kind") == "system"]
    assert brain.ledger_rows()[-1]["kind"] == "route"


def test_route_sync_no_banner_without_running_loop(cfg, tmp_volume) -> None:
    brain, _model = _brain(
        cfg,
        [sample("route_invalid_1"), sample("route_invalid_2"), sample("fallback_route")],
    )
    session = _session(tmp_volume, cfg)
    brain.route("fix the bug", session=session)
    lines = read_jsonl(session.transcript_path)
    assert not [line for line in lines if line.get("kind") == "system"]


# ---------------------------------------------------------------------------
# 11: bus emission of route.decided
# ---------------------------------------------------------------------------


async def test_route_async_publishes_route_decided(cfg, tmp_volume) -> None:
    bus = Bus()
    brain, _model = _brain(cfg, [sample("route_valid")], bus=bus)
    session = _session(tmp_volume, cfg)
    async with bus.subscriber("t") as queue:
        await brain.route_async("hello there", session=session)
        topic, payload = await asyncio.wait_for(queue.get(), timeout=2.0)
    assert topic == "route.decided"
    assert payload == {"session_id": session.id, "route": "opencode"}
