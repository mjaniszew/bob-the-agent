# Taking UX captures (screenshots)

This document explains how to capture screenshots of the bob-harness gateway
UI for documentation and regression review. The DOM flow below is the
scripted login -> chat -> task board -> answer path; every step corresponds
to a DOM state that `tests/test_gateway_v2_surface.py::test_screenshot_ready_e2e`
pins on the static page (`bob_harness/gateway/ui.py` `PAGE`).

## Prerequisites

Bring the gateway up the same way the host smoke script does:

```bash
scripts/host-smoke.sh   # build/up + /healthz poll + smoke checks
```

or manually:

```bash
docker compose up -d agent      # UI served by bob_harness.gateway.app (compose service "agent", compose.yaml:56)
curl -fsS http://127.0.0.1:8181/healthz
```

The gateway base URL below is `http://127.0.0.1:8181` (the compose loopback publish, compose.yaml:63).

## 1. Login (key-for-cookie exchange, D6)

The UI never stores the API key: the browser exchanges it once for a
session cookie (`POST /api/login`), then the key is forgotten
(`sessionStorage` cleanup). Scripted login:

```bash
curl -fsS -c /tmp/bob-cookies.txt -H 'Content-Type: application/json' \
  -d '{"api_key": "<BOB_API_KEY>"}' http://127.0.0.1:8181/api/login
```

In the browser: open the base URL, paste the key into the login prompt,
submit. Capture the logged-in chat view after the exchange (login banner
absent, chat form focused).

## 2. Chat

Type a message into the chat form (`id="chat-form"`, input
`id="chat-text"`) and submit; output streams into `id="chatlog"
(class="panecols")`. Capture mid-stream and after the final message for the
chat pane states.

## 3. Task board

The tasks pane is a live htmx poller: `id="tasks"` issues
`hx-get="/api/tasks"` on `load, every 5s` and swaps `innerHTML`. Wait for at
least one poll cycle so the board is populated:

```bash
sleep 6
```

Capture the task board state. Each task row carries an answer form posting
to the answer endpoint (see the `data-answer-swap` attribute on the tasks
div, which advertises the htmx swap source used by the answer form).

## 4. Answer flow

Fill an answer form on a WAITING_INPUT task and submit; the answer POSTs to
`/api/tasks/answer/<task_id>`, the gate unblocks, and the task resumes
(WAITING_INPUT -> RUNNING). Capture the answered state and the resumed
run.

## 5. Fallback banner + reset affordance

When the relay/model is unreachable the page shows the fallback banner
`id="fallback-banner"` with the reset affordance `id="reset-banner"`.
Force it by stopping the backend relay, capture the banner, then press
reset and capture the recovered state.

## Session/materials panes

`sessions` (`/api/sessions`, every 5s) and `materials` (`/materials`, every
30s) panes poll like tasks; capture them populated for the full-board shot.

## SCREENSHOTS checklist

For each capture above, save under `docs/captures/` with a dated name, e.g.
`docs/captures/2026-11-25-login.png`, `...-chat-stream.png`,
`...-task-board.png`, `...-answer-resume.png`, `...-fallback-banner.png`.
Screenshot tooling: any headless browser works, e.g.

```bash
chromium --headless --disable-gpu --window-size=1280,900 \
  --screenshot=docs/captures/task-board.png http://127.0.0.1:8181/
```

After login the cookie jar from the exchange must be used by the browser
profile; DOM-state assertions (the pinned ids above) are the source of
truth that the page is in a capturable state before each screenshot.
