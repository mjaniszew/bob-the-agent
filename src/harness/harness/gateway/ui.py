"""Single-file gateway UI (DECISIONS.md D8, plan phase R6).

:func:`render_page` serves :data:`PAGE` — one static HTML string assembled at
import time (a constant, not a template) — for the ``GET /`` route registered
in :mod:`harness.gateway.app`.

Design notes (bounded polish per D8):

- htmx is VENDORED only: the single script tag loads ``/static/htmx.min.js``
  served by the gateway; there are ZERO external/CDN URLs in the document.
- SSE: htmx's ``hx-ext="sse"`` alternative was REJECTED — the sse extension
  needs ``hx-swap="sse:..."`` / ``sse-swap`` attributes and a per-event server
  shape, while the plain ``EventSource`` JavaScript below is about 40 lines
  and dependency-free beyond htmx itself.
- Login (D6/``5.2``): the page's inline JS prompts once for the API key and
  exchanges it for the HttpOnly ``harness_session`` cookie via ``POST
  /api/login``; the key is kept ONLY in ``sessionStorage`` — the raw key never
  reaches the wire again after the exchange and the cookie never carries it.
- Fallback banner (m4b web half, section 7.3): shows permanently when a
  ``reply.chunk`` with ``source == "system"`` or a ``reply.final`` with
  ``meta.fallback`` arrives; the reset button merely hides it CLIENT-SIDE and
  documents that the NEXT message routes normally again (design 7.3: "when
  quota returns, nothing to migrate") — no API call is invented. The
  ledger-backed half is the R5 transcript ``system`` banner data.
- The chat form keeps the server round-trip minimal: ``POST /api/chat`` is
  called by htmx (it auto-sends ``HX-Request``); the gateway answers an htmx
  request with a tiny route-line fragment, and the after-request handler uses
  its ``data-session-id`` to pin the hidden ``session_id`` input and open the
  ``EventSource`` stream. Regular (non-hx) clients still get the plain JSON.
- Session/materials panes poll through htmx and insert the raw JSON/text
  response bodies (plain, bounded polish).

``render_page`` returns a ``Response`` so the app can serve the same string
byte-for-byte without a template engine.
"""

from __future__ import annotations

from starlette.responses import Response

__all__ = ["PAGE", "render_page"]

PAGE: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>harness</title>
<script src="/static/htmx.min.js"></script>
<style>
  body { font-family: sans-serif; margin: 0 8px 24px; color: #1b1b1f; }
  header { display: flex; justify-content: space-between; align-items: baseline;
           border-bottom: 2px solid #333; margin-bottom: 10px; }
  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
  .pane { border: 1px solid #bbb; border-radius: 6px; padding: 8px; min-height: 40px; }
  .pane h2 { margin: 2px 0 8px; font-size: 14px; letter-spacing: 1px; }
  .panecols { font-size: 12px; white-space: pre-wrap; word-break: break-word;
              max-height: 420px; overflow: auto; background: #fafafa; padding: 6px; }
  #chatlog { height: 300px; }
  .banner { display: none; background: #fde68a; border: 1px solid #b45309;
            padding: 8px; margin: 8px 0; border-radius: 4px; }
  .banner button { margin-left: 8px; }
  form#chat-form { display: flex; gap: 6px; margin-top: 8px; }
  form#chat-form input[type=text] { flex: 1; }
  .line { margin: 2px 0; }
  .line.router { color: #0b57a4; }
  .line.system { color: #b45309; }
  .line.final { color: #11693e; }
  .muted { color: #666; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>harness</h1>
  <div>
    <span class="muted" id="session-label">no session yet</span>
    <span class="muted" id="stream-status">stream: off</span>
  </div>
</header>

<div id="fallback-banner" class="banner">
  <span>routed by fallback rules</span>
  <button id="reset-banner" type="button">route this session normally again</button>
</div>

<div class="grid">
  <div>
    <div class="pane">
      <h2>chat</h2>
      <div class="panecols" id="chatlog"></div>
      <form id="chat-form" hx-post="/api/chat" hx-target="#chatlog" hx-swap="beforeend">
        <input type="text" name="text" id="chat-text" autocomplete="off"
               placeholder="message the harness..." required>
        <input type="hidden" name="session_id" id="session-id-input" value="">
        <button type="submit">send</button>
      </form>
    </div>
    <div class="pane">
      <h2>tasks</h2>
      <div id="tasks" hx-get="/api/tasks" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>
    </div>
  </div>
  <div>
    <div class="pane">
      <h2>sessions</h2>
      <div class="panecols" id="sessions" hx-get="/api/sessions" hx-trigger="load, every 5s"></div>
    </div>
    <div class="pane">
      <h2>materials</h2>
      <div class="panecols" id="materials" hx-get="/materials" hx-trigger="load, every 30s"></div>
    </div>
  </div>
</div>

<script>
(function () {
  "use strict";
  var sessionId = null;
  var evtSource = null;

  function el(id) { return document.getElementById(id); }

  function appendLine(kind, text) {
    var log = el("chatlog");
    var div = document.createElement("div");
    div.className = "line " + (kind || "");
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function showBanner() {
    el("fallback-banner").style.display = "block";
  }

  // Reset affordance (section 7.3): the next message routes normally again;
  // there is nothing to migrate, so this only dismisses the banner client-side.
  el("reset-banner").addEventListener("click", function () {
    el("fallback-banner").style.display = "none";
  });

  function setStatus(text) { el("stream-status").textContent = "stream: " + text; }

  function openStream(sid) {
    if (evtSource) { evtSource.close(); evtSource = null; }
    if (!sid) { setStatus("off"); return; }
    evtSource = new EventSource("/api/chat/stream?session_id=" + sid);
    el("session-label").textContent = "session " + sid;
    setStatus("connecting");
    evtSource.addEventListener("reply.chunk", function (ev) {
      var payload = JSON.parse(ev.data);
      if (payload.source === "system") { showBanner(); return; }
      appendLine(payload.source === "router" ? "router" : "agent", payload.text);
      setStatus("live");
    });
    evtSource.addEventListener("reply.final", function (ev) {
      var payload = JSON.parse(ev.data);
      if (payload.meta && payload.meta.fallback) { showBanner(); }
      if (payload.meta && payload.meta.failed) { appendLine("error", payload.text); }
      else { appendLine("final", payload.text); }
    });
    evtSource.addEventListener("session.status", function (ev) {
      var payload = JSON.parse(ev.data);
      appendLine("muted", "state: " + payload.old + " > " + payload.new);
    });
    // Heartbeat marker: the stream is alive; shown in the status bar only.
    evtSource.addEventListener("heartbeat", function () { setStatus("alive"); });
    evtSource.onerror = function () { setStatus("interrupted (reconnects auto)"); };
  }

  function login(key) {
    return fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ "api_key": key })
    });
  }

  function askKey() {
    var key = window.prompt("API key for the harness gateway:");
    if (!key) { appendLine("error", "no API key given; requests will fail"); return; }
    login(key).then(function (resp) {
      if (resp && resp.ok) {
        sessionStorage.setItem("harness_key", "set");
        appendLine("muted", "signed in: cookie issued");
      } else {
        appendLine("error", "login rejected (" + (resp ? resp.status : "network") + ")");
        sessionStorage.removeItem("harness_key");
      }
    });
  }

  el("chat-form").addEventListener("htmx:afterRequest", function (evt) {
    var detail = evt.detail;
    var xhr = detail.xhr;
    if (!xhr) { return; }
    if (xhr.status === 401) {
      sessionStorage.removeItem("harness_key");
      appendLine("error", "unauthorized: asking again for the key");
      askKey();
      return;
    }
    if (detail.successful === false) {
      appendLine("error", "request failed: " + xhr.status);
      return;
    }
    var sid = null;
    var body = xhr.responseText || "";
    // htmx requests receive the route-line fragment; API callers receive JSON.
    if (body.charAt(0) === "<") {
      var doc = new DOMParser().parseFromString(body, "text/html");
      var node = doc.querySelector("[data-session-id]");
      if (node) { sid = node.getAttribute("data-session-id"); }
    } else {
      try {
        var data = JSON.parse(body);
        sid = data.session_id;
        var route = data.route || {};
        appendLine("router", "[routed to " + (route.agent || "?") + "] " +
                            (route.task_title || ""));
      } catch (err) { /* non-JSON body: nothing to pin */ }
    }
    if (sid && sid !== sessionId) {
      sessionId = sid;
      el("session-id-input").value = sid;
      openStream(sid);
    }
  });

  if (!sessionStorage.getItem("harness_key")) { askKey(); }
}());
</script>
</body>
</html>
"""


def render_page() -> Response:
    """The ``GET /`` response: :data:`PAGE` as ``text/html`` (unauthenticated —
    the page itself drives the key-for-cookie exchange via ``POST /api/login``)."""
    return Response(PAGE, media_type="text/html")
