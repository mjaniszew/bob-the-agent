"""Gateway app tests (TestClient, zero network; TEST_PLAN rows 12/13/15/16).

The router seam is the R5 fake model OBJECT (``tests.mocks.router_model``,
plan R5 REVIEW R-3) injected into ``RouterBrain``; the adapter seam is the
injected :class:`ScriptedAdapter` (FakeAdapter-like object whose ``run(spec)``
returns an async iterator of :class:`StreamEvent`) — exactly the R-3 seams the
plan names for the R6 gateway.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.adapters.base import RunSpec, StreamEvent
from harness.bus import Bus
from harness.config import HarnessConfig, RateLimitConfig
from harness.gateway.app import create_app
from harness.persist import JsonlWriter, read_jsonl
from harness.router import RouterBrain
from harness.session_manager import SessionManager
from tests.mocks.router_model import make_fake_model

API_KEY = "test-key-123"  # conftest tmp_volume feeds HARNESS_API_KEY with this
HEADERS = {"X-API-Key": API_KEY}

_MEMORY_SENTINEL = "MEMORY_SENTINEL_XYZ"


class ScriptedAdapter:
    """FakeAdapter-like test adapter: replays a fixed ``StreamEvent`` list,
    awaiting ``delay_s`` before each event so tests control stream timing."""

    def __init__(self, events: list[StreamEvent], delay_s: float = 0.15) -> None:
        self._events = events
        self._delay_s = delay_s
        self.specs: list[RunSpec] = []

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        self.specs.append(spec)
        for event in self._events:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield event


def make_app(
    cfg: HarnessConfig,
    *,
    outputs: list | None = None,
    events: list[StreamEvent] | None = None,
    adapters: object = None,
    rate_limit: RateLimitConfig | None = None,
    delay_s: float = 0.15,
) -> tuple:
    """Fresh app per test: fake router model + a scripted adapter behind the
    R-3 seams (never a real provider/model call)."""
    if rate_limit is not None:
        cfg = cfg.model_copy(update={"rate_limit": rate_limit})
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    model_outputs = outputs if outputs is not None else [
        {"agent": "opencode", "task_title": "demo task"}
    ]
    brain = RouterBrain(cfg, model=make_fake_model(model_outputs))
    adapter = ScriptedAdapter(
        events
        if events is not None
        else [
            StreamEvent(kind="chunk", text="a"),
            StreamEvent(kind="chunk", text="b"),
            StreamEvent(kind="final", final_text="done"),
        ],
        delay_s=delay_s,
    )
    if adapters is None:
        adapters = {"opencode": adapter, "hermes": adapter}
    app = create_app(cfg, bus, manager, router_brain=brain, adapters=adapters)
    return app, adapter, bus, manager


def parse_sse(lines: list[str]) -> list[dict]:
    """Parse SSE wire lines into event dicts (field order irrelevant)."""
    events: list[dict] = []
    current: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            if current:
                events.append(current)
                current = {}
            continue
        key, _, value = line.partition(": ")
        current[key] = value
    if current:
        events.append(current)
    return events


def read_until_final(stream_response) -> list[str]:
    """SSE lines through (and including) the ``reply.final`` data line."""
    lines: list[str] = []
    it = stream_response.iter_lines()
    seen_final_event = False
    for line in it:
        lines.append(line)
        if "event: reply.final" in line:
            seen_final_event = True
        elif seen_final_event and line.startswith("data: "):
            break
    return lines


def transcript_records(cfg: HarnessConfig, session_id: str) -> list[dict]:
    return read_jsonl(Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl")


def wait_idle(client: TestClient, session_id: str, timeout_s: float = 6.0) -> str:
    deadline = time.monotonic() + timeout_s
    state = ""
    while time.monotonic() < deadline:
        state = client.get(f"/api/session/{session_id}", headers=HEADERS).json().get("state")
        if state == "idle":
            break
        time.sleep(0.05)
    return state


# ----------------------------------------------------------------------
# Auth matrix (row 12)
# ----------------------------------------------------------------------
def test_healthz_open(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"ok": True}  # no version echo, no extra fields


def test_auth_matrix(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.get("/api/sessions")
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "APIKey"
        wrong = client.get("/api/sessions", headers={"X-API-Key": "wrong-key"})
        assert wrong.status_code == 401
        okay = client.get("/api/sessions", headers=HEADERS)
        assert okay.status_code == 200
        assert okay.json() == []
        # The page shell renders for ANY caller (the inline JS drives login).
        page = client.get("/", headers={"X-API-Key": "wrong-key"})
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")


def test_login_cookie_flow(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        bad = client.post("/api/login", json={"api_key": "not-the-key"})
        assert bad.status_code == 401
        good = client.post("/api/login", json={"api_key": API_KEY})
        assert good.status_code == 200
        assert good.json() == {"ok": True}
        set_cookie = good.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "samesite=lax" in set_cookie
        cookie = client.cookies.get("harness_session")
        assert cookie
        assert API_KEY not in cookie  # the cookie NEVER carries the raw key
        via_cookie = client.get("/api/sessions")  # cookie only, no header
        assert via_cookie.status_code == 200
        via_form = client.post("/api/login", data={"api_key": API_KEY})
        assert via_form.status_code == 200


def test_not_found_not_403(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        assert client.get("/api/session/ZZZZZZZZ", headers=HEADERS).status_code == 404
        assert client.get("/api/session/..%2f..%2fetc", headers=HEADERS).status_code == 404
        assert client.get("/api/session/" + "q" * 200, headers=HEADERS).status_code == 404
        assert client.get("/api/chat/stream?session_id=short", headers=HEADERS).status_code == 404
        assert (
            client.post(
                "/api/chat", json={"text": "hi", "session_id": "NOPE"}, headers=HEADERS
            ).status_code
            == 404
        )


def test_rate_limit_login_burst(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg, rate_limit=RateLimitConfig(per_minute=3, stream_per_minute=600))
    with TestClient(app) as client:
        codes = [
            client.post("/api/login", json={"api_key": "nope"}).status_code for _ in range(4)
        ]
        assert codes[:3] == [401, 401, 401]
        assert codes[3] == 429


# ----------------------------------------------------------------------
# SSE order + heartbeat + transcript fidelity (rows 15/16)
# ----------------------------------------------------------------------
def test_sse_order_and_heartbeat(tmp_volume: Path, cfg: HarnessConfig) -> None:
    events = [
        StreamEvent(kind="started"),
        StreamEvent(kind="chunk", text="a"),
        StreamEvent(kind="chunk", text="b"),
        StreamEvent(kind="final", final_text="done"),
    ]
    app, *_ = make_app(cfg, outputs=[{"agent": "opencode", "task_title": "fix things"}],
                       events=events, delay_s=0.15)
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"text": "fix the bug"}, headers=HEADERS)
        assert first.status_code == 200
        session_id = first.json()["session_id"]
        assert wait_idle(client, session_id) == "idle"
        url = f"/api/chat/stream?session_id={session_id}&heartbeat_s=0.2"
        with client.stream("GET", url, headers=HEADERS) as response:
            time.sleep(0.3)  # the sink must subscribe before the next turn
            second = client.post(
                "/api/chat",
                json={"text": "implement the fix", "session_id": session_id},
                headers=HEADERS,
            )
            assert second.status_code == 200
            lines: list[str] = []
            it = response.iter_lines()
            for line in it:
                lines.append(line)
                if "reply.final" in line:
                    break
            for _ in range(6):  # keep reading: a heartbeat lands within ~2x window
                try:
                    lines.append(next(it))
                except StopIteration:  # pragma: no cover — the stream stays open
                    break
                if "heartbeat" in lines[-1]:
                    break
        events_seen = parse_sse(lines)
        interesting = [
            (event["event"], json.loads(event["data"]))
            for event in events_seen
            if event.get("event") in ("reply.chunk", "reply.final")
        ]
        assert [name for name, _payload in interesting] == [
            "reply.chunk",
            "reply.chunk",
            "reply.chunk",
            "reply.final",
        ]
        router_chunk = interesting[0][1]
        assert router_chunk["source"] == "router" and router_chunk["seq"] == 0
        assert "[routed to opencode]" in router_chunk["text"]
        chunks = interesting[1:3]
        assert [payload["text"] for _event, payload in chunks] == ["a", "b"]
        assert [payload["seq"] for _event, payload in chunks] == [1, 2]
        assert [payload["source"] for _event, payload in chunks] == ["agent", "agent"]
        assert interesting[3][1]["text"] == "done"
        assert "failed" not in interesting[3][1]["meta"]
        assert any("heartbeat" in line for line in lines)


def test_round_trip_bus_transcript_mapping(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, _adapter, _bus, _manager = make_app(cfg, delay_s=0.2)
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"text": "refactor the parser"}, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"session_id", "state", "route"}
        session_id = body["session_id"]
        assert body["route"]["agent"] == "opencode"
        url = f"/api/chat/stream?session_id={session_id}&heartbeat_s=15"
        with client.stream("GET", url, headers=HEADERS) as stream:
            lines = read_until_final(stream)
            events_seen = parse_sse(lines)
        chunk_texts = [
            json.loads(event["data"])["text"]
            for event in events_seen
            if event.get("event") == "reply.chunk"
        ]
        finals = [
            json.loads(event["data"]) for event in events_seen if event.get("event") == "reply.final"
        ]
        assert chunk_texts == ["a", "b"]
        assert finals and finals[0]["text"] == "done"
        records = transcript_records(cfg, session_id)
        chunks = [record for record in records if record.get("kind") == "agent_chunk"]
        assert [record["data"]["text"] for record in chunks] == ["a", "b"]
        assert [record["data"]["seq"] for record in chunks] == [1, 2]
        final_records = [record for record in records if record.get("kind") == "agent_final"]
        assert final_records and final_records[-1]["data"]["text"] == "done"
        assert any(record.get("kind") == "router" for record in records)
        assert any(
            record.get("kind") == "user" and record["data"]["text"] == "refactor the parser"
            for record in records
        )


def test_chat_form_encoded(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.post("/api/chat", data={"text": "hello via the htmx form"},
                               headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["route"]["agent"] == "opencode"


def test_chat_validation_and_guard(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        guard = client.post("/api/chat", json={"text": "x", "session_id": "bad id!"}, headers=HEADERS)
        assert guard.status_code == 404
        assert client.post("/api/chat", json={"text": ""}, headers=HEADERS).status_code == 422
        assert client.post("/api/chat", json={"text": "   "}, headers=HEADERS).status_code == 422
        assert client.post("/api/chat", json={}, headers=HEADERS).status_code == 422


# ----------------------------------------------------------------------
# Fallback banner (m4b web half, 7.3)
# ----------------------------------------------------------------------
def test_fallback_banner_marker(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(
        cfg,
        outputs=["garbage-not-json"],  # every schema call fails -> keyword fallback
        events=[
            StreamEvent(kind="chunk", text="note one"),
            StreamEvent(kind="final", final_text="fallback final"),
        ],
        delay_s=0.1,
    )
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"text": "deploy the server"}, headers=HEADERS)
        assert first.status_code == 200
        session_id = first.json()["session_id"]
        assert wait_idle(client, session_id) == "idle"
        url = f"/api/chat/stream?session_id={session_id}&heartbeat_s=0.2"
        with client.stream("GET", url, headers=HEADERS) as stream:
            time.sleep(0.3)
            second = client.post(
                "/api/chat",
                json={"text": "deploy again", "session_id": session_id},
                headers=HEADERS,
            )
            assert second.status_code == 200
            lines: list[str] = []
            for line in stream.iter_lines():
                lines.append(line)
                if "reply.final" in line:
                    break
            events_seen = parse_sse(lines)
        system_chunks = [
            json.loads(event["data"])
            for event in events_seen
            if event.get("event") == "reply.chunk"
            and json.loads(event["data"]).get("source") == "system"
        ]
        assert system_chunks
        assert "routed by fallback rules" in system_chunks[0]["text"]
        records = transcript_records(cfg, session_id)
        banners = [
            record
            for record in records
            if record.get("kind") == "system"
            and "routed by fallback" in str((record.get("data") or {}).get("note", ""))
        ]
        assert len(banners) >= 2  # the R5 ledger-backed transcript banner, both turns


# ----------------------------------------------------------------------
# Handoff proxy
# ----------------------------------------------------------------------
def test_handoff_creates_child_and_runs(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, _adapter, _bus, _manager = make_app(cfg)
    with TestClient(app) as client:
        parent = client.post("/api/chat", json={"text": "start the work"}, headers=HEADERS)
        parent_id = parent.json()["session_id"]
        handoff_text = "```HANDOFF\nAGENT: opencode\nPROMPT: finish the follow-up\n```"
        child_resp = client.post(
            "/api/handoff", json={"session_id": parent_id, "text": handoff_text}, headers=HEADERS
        )
        assert child_resp.status_code == 200
        child = child_resp.json()
        assert child["parent_session"] == parent_id
        child_id = child["session_id"]
        assert child_id != parent_id
        assert wait_idle(client, child_id) == "idle"
        child_records = transcript_records(cfg, child_id)
        assert any(record.get("kind") == "session_link" for record in child_records)
        assert any(
            record.get("kind") == "agent_final" and record["data"]["text"] == "done"
            for record in child_records
        )
        invalid = client.post(
            "/api/handoff", json={"session_id": parent_id, "text": "no fence here"}, headers=HEADERS
        )
        assert invalid.status_code == 422


# ----------------------------------------------------------------------
# Never-exposed negatives (row 12)
# ----------------------------------------------------------------------
def _write_negatives(cfg: HarnessConfig) -> tuple[str, str]:
    memory_dir = Path(cfg.storage.data_dir) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    first30 = "SECRET-DIGEST-HEAD-ZER0"
    content = first30 + f" plus {_MEMORY_SENTINEL} body that must never be served"
    (memory_dir / "MEMORY.md").write_text(content, encoding="utf-8")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = {
        "ts": f"{today}T10:00:00Z",
        "kind": "route",
        "agent": "opencode",
        "session_id": None,
        "fallback": False,
        "confidence": None,
        "latency_ms": 5,
        "model": "fake-router",
    }
    JsonlWriter(Path(cfg.storage.data_dir) / "state" / "router_ledger.jsonl").append(row)
    return first30, content


def test_never_exposed_negatives(tmp_volume: Path, cfg: HarnessConfig) -> None:
    first30, content = _write_negatives(cfg)
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"text": "please summarize"}, headers=HEADERS)
        assert chat.status_code == 200
        session_id = chat.json()["session_id"]
        materials = client.get("/materials", headers=HEADERS).text
        sessions = json.dumps(client.get("/api/sessions", headers=HEADERS).json())
        transcript = json.dumps(client.get(f"/api/session/{session_id}", headers=HEADERS).json())
        chat_body = json.dumps(chat.json())
        for body in (materials, sessions, transcript, chat_body):
            assert _MEMORY_SENTINEL not in body
            assert first30 not in body
            assert API_KEY not in body
            assert "DISCORD_BOT_TOKEN" not in body
        # the materials report carries the SIZE but never the CONTENT
        assert f"MEMORY.md: {len(content)} chars" in materials
        assert '{"ts"' not in materials  # no raw router-ledger rows
        assert "/app/projects" not in materials


# ----------------------------------------------------------------------
# Materials shape (row 12/13)
# ----------------------------------------------------------------------
def test_materials_shape(tmp_volume: Path, cfg: HarnessConfig) -> None:
    memory_dir = Path(cfg.storage.data_dir) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    mem_content = "plain-memory-body"  # 17 ascii chars == the byte size
    (memory_dir / "MEMORY.md").write_text(mem_content, encoding="utf-8")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = {
        "ts": f"{today}T10:00:00Z",
        "kind": "route",
        "agent": "hermes",
        "session_id": None,
        "fallback": True,
        "confidence": None,
        "latency_ms": 7,
        "model": "fake-router",
    }
    JsonlWriter(Path(cfg.storage.data_dir) / "state" / "router_ledger.jsonl").append(row)
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.get("/materials", headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        text = response.text
        for heading in ("sessions:", "memory:", "skills:", "tasks:"):
            assert heading in text
        assert f"MEMORY.md: {len(mem_content)} chars" in text
        assert "tasks: 0" in text  # live render: the fixture creates no tasks
        assert "runs:" not in text  # the runs ledger lands with R9/R10
        payload = client.get("/materials?format=json", headers=HEADERS).json()
        assert payload["memory"][0]["chars"] == len(mem_content)
        assert payload["router"] == {"calls": 1, "fallback": 1}


# ----------------------------------------------------------------------
# Static htmx asset
# ----------------------------------------------------------------------
def test_static_htmx_served(tmp_volume: Path, cfg: HarnessConfig) -> None:
    placeholder = b"/* htmx vendored binary missing - orchestrator to replace */"
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.get("/static/htmx.min.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.content
        if placeholder in response.content:
            pytest.skip("htmx placeholder bytes present; orchestrator to replace")
        assert len(response.content) > 10000


# ----------------------------------------------------------------------
# Stream parameter edges
# ----------------------------------------------------------------------
def test_stream_heartbeat_validation(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"text": "hello"}, headers=HEADERS)
        session_id = chat.json()["session_id"]
        bad = client.get(
            f"/api/chat/stream?session_id={session_id}&heartbeat_s=0",
            headers=HEADERS,
        )
        assert bad.status_code == 422
        big = client.get(
            f"/api/chat/stream?session_id={session_id}&heartbeat_s=120",
            headers=HEADERS,
        )
        assert big.status_code == 422


def test_stream_resume_filter(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg, delay_s=0.15)
    with TestClient(app) as client:
        chat = client.post("/api/chat", json={"text": "hello again"}, headers=HEADERS)
        session_id = chat.json()["session_id"]
        assert wait_idle(client, session_id) == "idle"
        url = f"/api/chat/stream?session_id={session_id}&seq=1&heartbeat_s=0.2"
        with client.stream("GET", url, headers=HEADERS) as stream:
            time.sleep(0.3)
            second = client.post(
                "/api/chat",
                json={"text": "one more code fix", "session_id": session_id},
                headers=HEADERS,
            )
            assert second.status_code == 200
            lines: list[str] = []
            it = stream.iter_lines()
            for line in it:
                lines.append(line)
                if "reply.final" in line:
                    break
            for _ in range(2):  # pull the final's data line (the loop broke on its event line)
                try:
                    lines.append(next(it))
                except StopIteration:  # pragma: no cover — the stream stays open
                    break
        events_seen = parse_sse(lines)
        interesting = [
            (event["event"], json.loads(event["data"]))
            for event in events_seen
            if event.get("event") in ("reply.chunk", "reply.final")
        ]
        # resume cursor seq=1: the router chunk (seq 0) and chunk a (seq 1) are
        # dropped on this connection; only later events arrive.
        agent_chunks = [payload for name, payload in interesting if name == "reply.chunk"]
        assert [payload["seq"] for payload in agent_chunks] == [2]
        assert agent_chunks[0]["text"] == "b"
        assert any(name == "reply.final" for name, _payload in interesting)
