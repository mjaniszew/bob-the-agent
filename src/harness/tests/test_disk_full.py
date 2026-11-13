"""F7 disk-full (ENOSPC) on the chat write endpoint -> 503, reads stay 200.

Test A patches the per-session transcript writer seam
(``harness.persist.JsonlWriter.append``); Test B patches the real state/meta
write seam ``harness.persist.AtomicCounter._write_atomic``. There is no
``StateFile`` class in this tree: ``Session.rename`` -> ``Session._write_meta``
-> ``AtomicCounter.set`` -> ``AtomicCounter._write_atomic``. On a fresh-session
POST the same seam is hit earlier by ``Session.create`` (meta.json), so the 503
covers the state-write boundary without separately reaching rename.
"""

from __future__ import annotations

import asyncio
import errno
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness import persist
from harness.adapters.base import RunSpec, StreamEvent
from harness.bus import Bus
from harness.config import HarnessConfig, RateLimitConfig
from harness.gateway.app import create_app
from harness.persist import read_jsonl
from harness.router import RouterBrain
from harness.session_manager import SessionManager
from tests.mocks.router_model import make_fake_model

API_KEY = "test-key-123"  # conftest tmp_volume feeds HARNESS_API_KEY with this
HEADERS = {"X-API-Key": API_KEY}


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
    model_outputs = (
        outputs
        if outputs is not None
        else [{"agent": "opencode", "task_title": "demo task"}]
    )
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


def wait_idle(client: TestClient, session_id: str, timeout_s: float = 6.0) -> str:
    deadline = time.monotonic() + timeout_s
    state = ""
    while time.monotonic() < deadline:
        state = client.get(f"/api/session/{session_id}", headers=HEADERS).json().get("state")
        if state == "idle":
            break
        time.sleep(0.05)
    return state


def _enospc(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def test_chat_write_disk_full_returns_503_and_reads_stay_200(
    tmp_volume: Path, cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, *_ = make_app(cfg, delay_s=0.0)
    with TestClient(app) as client:
        control = client.post(
            "/api/chat", json={"text": "before the disk fills"}, headers=HEADERS
        )
        assert control.status_code == 200
        session_id = control.json()["session_id"]
        assert wait_idle(client, session_id) == "idle"
        transcript_path = (
            Path(cfg.storage.data_dir) / "sessions" / session_id / "transcript.jsonl"
        )
        before = read_jsonl(transcript_path)
        assert before

        with monkeypatch.context() as patch:
            patch.setattr(persist.JsonlWriter, "append", _enospc)
            failed = client.post(
                "/api/chat",
                json={"text": "this write hits a full disk", "session_id": session_id},
                headers=HEADERS,
            )
        assert failed.status_code == 503
        assert failed.json()["detail"] == "storage failure"

        after = read_jsonl(transcript_path)
        assert after == before
        assert all(isinstance(record, dict) for record in after)
        assert any(record.get("kind") == "user" for record in after)
        physical = [
            json.loads(line)
            for line in transcript_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert all(isinstance(record, dict) for record in physical)

        read = client.get(f"/api/session/{session_id}", headers=HEADERS)
        assert read.status_code == 200
        assert read.json()["events"]


def test_meta_write_disk_full_returns_503(
    tmp_volume: Path, cfg: HarnessConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, *_ = make_app(cfg, delay_s=0.0)
    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr(persist.AtomicCounter, "_write_atomic", _enospc)
            failed = client.post(
                "/api/chat", json={"text": "fresh session"}, headers=HEADERS
            )
        assert failed.status_code == 503
        assert failed.json()["detail"] == "storage failure"

        listing = client.get("/api/sessions", headers=HEADERS)
        assert listing.status_code == 200
