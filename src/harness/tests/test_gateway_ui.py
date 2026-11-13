"""Gateway UI single-file tests (DECISIONS.md D8; TEST_PLAN row 13)."""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import Response
from fastapi.testclient import TestClient

from harness.config import HarnessConfig
from harness.gateway.ui import PAGE, render_page
from harness.tasks import TaskDir, TaskStore
from tests.test_gateway_app import HEADERS, make_app  # reuse the app harness


def test_page_markers() -> None:
    for marker in (
        'hx-post="/api/chat"',
        'hx-target="#chatlog"',
        'hx-swap="beforeend"',
        'hx-get="/api/sessions"',
        'hx-get="/materials"',
        'hx-trigger="load, every 5s"',
        'hx-trigger="load, every 30s"',
        "/api/chat/stream",
        'id="sessions"',
        'id="tasks"',
        'id="materials"',
        'id="fallback-banner"',
        'id="chatlog"',
        'id="reset-banner"',
        'name="text"',
        'name="session_id"',
        '/static/htmx.min.js',
    ):
        assert marker in PAGE, f"missing UI marker: {marker}"


def test_page_zero_external_urls() -> None:
    for needle in ("src=\"http", "href=\"http", "unpkg", "jsdelivr", "cdn", "http://", "https://"):
        assert needle not in PAGE, f"external reference found: {needle}"


def test_banner_semantics() -> None:
    # banner div + the JS branch that raises it (>= 2 occurrences, row 13)
    assert PAGE.count("fallback") >= 2
    assert 'id="fallback-banner"' in PAGE
    assert "routed by fallback rules" in PAGE
    assert 'payload.source === "system"' in PAGE  # reply.chunk fallback marker
    assert "meta.fallback" in PAGE  # reply.final fallback marker
    assert "meta.failed" in PAGE  # honest failed-final relay


def test_reset_is_client_side_only() -> None:
    index = PAGE.index('id="reset-banner"')
    snippet = PAGE[index : PAGE.index("</div>", index)]  # the banner block only
    assert "route this session normally again" in snippet
    assert "/api/handoff" not in snippet
    assert "/api/chat" not in snippet


def test_htmx_vendored_only() -> None:
    assert PAGE.count("<script src=") == 1
    assert '<script src="/static/htmx.min.js"></script>' in PAGE


def test_render_page_response() -> None:
    result = render_page()
    assert isinstance(result, Response)
    assert result.media_type == "text/html"
    assert result.body == PAGE.encode("utf-8")


def test_get_route_serves_page(tmp_volume: Path, cfg: HarnessConfig) -> None:
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        response = client.get("/", headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.text == PAGE


def test_tasks_pane_polls_api() -> None:
    # R11/U1: the tasks pane is a live htmx poller like the sessions pane.
    assert 'id="tasks" hx-get="/api/tasks"' in PAGE
    assert 'hx-trigger="load, every 5s" hx-swap="innerHTML"' in PAGE
    assert "coming in R10" not in PAGE


def test_tasks_fragment_roundtrip(tmp_volume: Path, cfg: HarnessConfig) -> None:
    # Del tmp_volume: cfg already points the TaskStore at the tmp volume.
    del tmp_volume
    store = TaskStore(cfg)
    task_id = store.create_inbox_task("plain created chore", origin="user")
    destination = store.move_from_inbox(task_id)
    TaskDir.birth(destination, cfg, origin="user")
    app, *_ = make_app(cfg)
    with TestClient(app) as client:
        hx = client.get("/api/tasks", headers={**HEADERS, "HX-Request": "true"})
        assert hx.status_code == 200
        assert hx.headers["content-type"].startswith("text/html")
        fragment = hx.text
        assert 'class="taskrow"' in fragment
        assert f"#{task_id}" in fragment
        assert ">new<" in fragment  # born state: the plain-text badge text
        assert "d0" in fragment  # depth from state.json
        assert "<form" not in fragment  # plain created task: NO answer form
        # The JSON path stays exactly as-is for non-hx callers.
        plain = client.get("/api/tasks", headers=HEADERS)
        assert plain.status_code == 200
        assert plain.headers["content-type"].startswith("application/json")
        assert plain.json()[0]["id"] == task_id
