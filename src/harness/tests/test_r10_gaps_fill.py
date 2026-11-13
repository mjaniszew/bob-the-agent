"""R10 gap fills (R11 WP-U2): the four tests R10 never shipped, one file.

- Gap 1 — POST /api/tasks/answer route matrix over TestClient: success 200,
  unknown id 404 (never 403), non-WAITING_INPUT 409, no wired taskloop 503.
  The wired success test runs the REAL TaskLoop on the SAME event loop as the
  app (the TestClient portal loop, started/stopped via the app lifespan) so
  answer() shares one loop with the gate asyncio primitives — exactly the
  supervisor wiring production uses.
- Gap 2 — GET /api/tasks plain JSON listing: empty store -> [] and populated
  rows with states/descriptions (WP-U1 covers only the htmx + one-row smoke).
- Gap 3 — the REAL F15 timer path: short ``tasks.gate_timeout_s`` override
  arms a genuine asyncio timer; ONE ``gate.notify`` kind=timeout event, the
  task FAILS with reason gate_timeout, and a second timeout window does not
  double-notify (the renotified guard).
- Gap 4 — bus-driven resume: the taskloop sink consumes a Discord-shaped
  ``gate.answered`` on the REAL Bus and the task resumes; combined
  app-level check: TaskLoop wired to create_app, GET /api/task/{id} shows
  the state left WAITING_INPUT.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.adapters.base import RunSpec, StreamEvent
from harness.bus import Bus
from harness.config import HarnessConfig
from harness.gateway.app import create_app
from harness.persist import AtomicCounter
from harness.router import RouterBrain
from harness.session_manager import SessionManager
from harness.taskloop import TaskLoop
from harness.tasks import TaskDir, TaskStore
from tests.mocks.router_model import make_fake_model
from tests.test_gateway_app import HEADERS, make_app
from tests.test_taskloop import (
    _T1,
    _T2,
    _analysis,
    _Bus,
    _events_of,
    _factory,
    _final,
    _kinds_of,
    _Router,
    _tweak,
    _wait_state,
)


def _fake_brain(cfg: HarnessConfig) -> RouterBrain:
    return RouterBrain(cfg, model=make_fake_model([{"agent": "hermes", "task_title": "demo"}]))


def _gate_question_raw(question: str) -> str:
    return json.dumps({"gate_question": question})


def _wire_loop_lifespan(app: FastAPI, loop: TaskLoop) -> None:
    """Start/stop the TaskLoop on the SAME event loop the app runs on (the
    TestClient portal loop): answer() then shares one loop with the gate
    locks/timers, mirroring the in-process supervisor wiring."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        loop.start()
        try:
            yield
        finally:
            await loop.stop()

    app.router.lifespan_context = lifespan


def _wired_idle_app(cfg: HarnessConfig) -> FastAPI:
    """App with a taskloop_ wired (never started; the 404/409 answer routes
    never reach answer()). Same auth harness as test_gateway_app."""
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    loop = TaskLoop(
        cfg, manager, bus=bus,
        adapter_factory=_factory([], lambda s, i: []),
        router=_Router(),
    )
    return create_app(
        cfg, bus, manager, router_brain=_fake_brain(cfg), adapters={}, taskloop_=loop
    )


def _wait_via_route(client: TestClient, task_id: str, want: str, timeout_s: float = 6.0) -> dict:
    deadline = time.monotonic() + timeout_s
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/task/{task_id}", headers=HEADERS)
        if response.status_code == 200:
            body = response.json()
            if body.get("state") == want:
                return body
        time.sleep(0.05)
    pytest.fail(f"task {task_id} never reached {want} via the route; got {body}")


# ----------------------------------------------------------------------
# Gap 1: POST /api/tasks/answer route matrix
# ----------------------------------------------------------------------
def test_answer_route_success_200(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("route answer marker", origin="user", task_id=_T1)
    runs: list[dict] = []

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        if index == 0:
            return [(0.0, StreamEvent(kind="system", raw=_gate_question_raw("which env?")))]
        return [(0.0, _final("final after route answer"))]

    loop = TaskLoop(
        cfg, manager, bus=bus,
        adapter_factory=_factory(runs, planner),
        router=_Router(_analysis("seed", mode="gate")),
    )
    app = create_app(
        cfg, bus, manager, router_brain=_fake_brain(cfg), adapters={}, taskloop_=loop
    )
    _wire_loop_lifespan(app, loop)
    with TestClient(app) as client:
        _wait_via_route(client, _T1, "WAITING_INPUT")
        response = client.post(
            f"/api/tasks/answer/{_T1}", json={"text": "staging"}, headers=HEADERS
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "task_id": _T1, "state": "DONE"}
        assert "final after route answer" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        assert len(runs) == 2
        assert "User answer" in runs[1]["prompt"]


def test_answer_route_unknown_id_404_not_403(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    app = _wired_idle_app(cfg)
    with TestClient(app) as client:
        unknown = client.post(
            f"/api/tasks/answer/{_T1}", json={"text": "staging"}, headers=HEADERS
        )
        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "unknown task id"
        guard = client.post(
            "/api/tasks/answer/bad!id", json={"text": "staging"}, headers=HEADERS
        )
        assert guard.status_code == 404
        assert guard.json()["detail"] == "not found"


def test_answer_route_non_waiting_409(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    app = _wired_idle_app(cfg)
    store = TaskStore(cfg)
    task_id = store.create_inbox_task("not waiting marker", origin="user", task_id=_T1)
    TaskDir.birth(store.move_from_inbox(task_id), cfg, origin="user")
    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/answer/{_T1}", json={"text": "staging"}, headers=HEADERS
        )
        assert response.status_code == 409
        assert "not WAITING_INPUT" in response.json()["detail"]
        assert "appeared" in response.json()["detail"]


def test_answer_route_503_when_not_wired(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/answer/ZZZ99999", json={"text": "hi"}, headers=HEADERS
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "task loop not wired"}
        bad = client.post(
            "/api/tasks/answer/bad!id", json={"text": "hi"}, headers=HEADERS
        )
        assert bad.status_code == 503


# ----------------------------------------------------------------------
# Gap 2: GET /api/tasks plain JSON listing
# ----------------------------------------------------------------------
def test_tasks_listing_json_empty_and_populated(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        empty = client.get("/api/tasks", headers=HEADERS)
        assert empty.status_code == 200
        assert empty.headers["content-type"].startswith("application/json")
        assert empty.json() == []

        store = TaskStore(cfg)
        first = store.create_inbox_task("first listing marker", origin="user", task_id=_T1)
        TaskDir.birth(store.move_from_inbox(first), cfg, origin="user")
        second = store.create_inbox_task("second listing marker", origin="user", task_id=_T2)
        TaskDir.birth(store.move_from_inbox(second), cfg, origin="user")
        AtomicCounter(store.root / _T2 / "state.json").set({"state": "RUNNING"})

        rows = client.get("/api/tasks", headers=HEADERS).json()
        assert [row["id"] for row in rows] == [_T1, _T2]
        by_id = {row["id"]: row for row in rows}
        assert by_id[_T1]["state"] == "appeared"
        assert by_id[_T2]["state"] == "RUNNING"
        assert by_id[_T1]["title"] == "first listing marker"
        assert by_id[_T2]["title"] == "second listing marker"
        assert by_id[_T1]["depth"] == 0 and by_id[_T1]["parent"] is None
        assert set(by_id[_T1]) == {"id", "state", "title", "depth", "parent", "updated"}


# ----------------------------------------------------------------------
# Gap 3: the REAL short-override gate timeout timer
# ----------------------------------------------------------------------
async def test_gate_timeout_short_override_one_notify_then_failed(
    tmp_volume: Any, cfg: HarnessConfig
) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05, gate_timeout_s=0.05)
    bus = _Bus()
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("short gate timeout marker", origin="user", task_id=_T1)
    runs: list[dict] = []
    raw = _gate_question_raw("which env?")
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus,
        adapter_factory=_factory(runs, lambda s, i: [(0.0, StreamEvent(kind="system", raw=raw))]),
        router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        await asyncio.sleep(0.15)  # the REAL 0.05 timer window + slack
        notifies = [payload for topic, payload in bus.events if topic == "gate.notify"]
        assert notifies == [{"task_id": _T1, "question": "which env?", "kind": "timeout"}]
        assert store.read_state(_T1)["state"] == "FAILED"
        assert any(
            event["kind"] == "state_change" and event["data"]["new"] == "FAILED"
            and event["data"].get("reason") == "gate_timeout"
            for event in _events_of(store, _T1)
        )
        await asyncio.sleep(0.15)  # a second window: no double-notify
        assert [payload for topic, payload in bus.events if topic == "gate.notify"] == notifies
    finally:
        await loop.stop()


# ----------------------------------------------------------------------
# Gap 4: bus-driven gate.answered -> resume + wired-app detail view
# ----------------------------------------------------------------------
async def test_bus_gate_answered_resumes_task(tmp_volume: Any, cfg: HarnessConfig) -> None:
    del tmp_volume
    cfg = _tweak(cfg, poll_seconds=0.05)
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    store = TaskStore(cfg, bus=bus)
    store.create_inbox_task("bus gate answer marker", origin="user", task_id=_T1)
    runs: list[dict] = []

    def planner(spec: RunSpec, index: int) -> list[tuple[float, StreamEvent]]:
        if index == 0:
            return [(0.0, StreamEvent(kind="system", raw=_gate_question_raw("which env?")))]
        return [(0.0, _final("resumed via bus answer"))]

    loop = TaskLoop(
        cfg, manager, bus=bus,
        adapter_factory=_factory(runs, planner),
        router=_Router(_analysis("seed", mode="gate")),
    )
    loop.start()
    try:
        await _wait_state(store, _T1, "WAITING_INPUT")
        # the Discord payload shape: bus.publish -> the taskloop sink consumes it
        await bus.publish(
            "gate.answered",
            {"task_id": f"T{_T1}", "answer": "staging", "channel": "discord"},
        )
        await _wait_state(store, _T1, "DONE")
        assert len(runs) == 2
        assert "User answer" in runs[1]["prompt"]
        assert "resumed via bus answer" in (
            store.root / _T1 / "result.md"
        ).read_text(encoding="utf-8")
        assert "gate_answered" in _kinds_of(store, _T1)

        app = create_app(
            cfg, bus, manager, router_brain=_fake_brain(cfg), adapters={}, taskloop_=loop
        )
        detail = TestClient(app).get(f"/api/task/{_T1}", headers=HEADERS)
        assert detail.status_code == 200
        body = detail.json()
        assert body["state"] == "DONE"  # the state left WAITING_INPUT
        assert any(event["kind"] == "gate_answered" for event in body["events"])
        assert body["result_md"] is True
    finally:
        await loop.stop()
