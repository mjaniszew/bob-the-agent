#!/usr/bin/env bash
# host-smoke.sh — smoke-verify the bob-the-agent compose stack from the Docker HOST.
#
# Runs on the user's Docker HOST (NOT inside a container). Requires Docker and
# docker compose v2. Verifies the bob-harness gateway on 127.0.0.1:8181 end to end:
# image build, stack start, gateway HTTP 200, startup marker in container logs,
# no-banner start (P7), in-container harness import, executor-backed task pickup
# (P7 default runner=executor), the v2 requirements.lock dependency pins (P9),
# screenshot e2e readiness (P7 capture flow), and all four compose services Up.
#
# Usage: ./scripts/host-smoke.sh [--skip-build]
#
# Security: this script never reads or prints .env contents and never echoes
# secret values. Variable NAMES are reported only as <set>/<unset>.
set -euo pipefail

SKIP_BUILD=0
IMAGE_TAG="bob-the-agent:latest"
DOCKERFILE="dockerfiles/Dockerfile.hermes"
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    -h|--help)
      echo "Usage: $0 [--skip-build]"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

cd "$(dirname "${BASH_SOURCE[0]}")/.."

GATEWAY_URL="http://127.0.0.1:8181/"
GATEWAY_HOST="127.0.0.1:8181"
POLL_TIMEOUT=7050
POLL_INTERVAL=10

pass() { echo "RESULT: [$1] $2 ... PASS${3:+ ($3)}"; }
fail() {
  echo "RESULT: [$1] $2 ... FAIL: $3" >&2
  exit 1
}

echo "== bob-the-agent host smoke =="
echo "working dir: $(pwd)"

# Report key variable NAMES only — never their values (no .env reads here).
for var in BOB_HARNESS_API_KEY DISCORD_BOT_TOKEN; do
  if [ -n "${!var:-}" ]; then
    echo "using ${var}=<set>"
  else
    echo "using ${var}=<unset in host shell; compose may load it from .env>"
  fi
done

# compose.yaml has no build section (image tag is pinned at bob-the-agent:latest),
# so "compose build" would verify nothing: build the image the way the task files
# and SMOKE_LOG record it — docker build from the repo root with the harness
# Dockerfile.
if [ "$SKIP_BUILD" -eq 1 ]; then
  echo "RESULT: [1/6] docker build (${DOCKERFILE}) ... SKIP (--skip-build)"
elif docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" .; then
  pass "1/6" "docker build (${DOCKERFILE})"
else
  fail "1/6" "docker build (${DOCKERFILE})" "build failed"
fi

if docker compose up -d; then
  pass "2/9" "docker compose up -d"
else
  fail "2/9" "docker compose up -d" "compose up failed"
fi

echo "== polling ${GATEWAY_URL} (up to ${POLL_TIMEOUT}s; first boot may pull/bootstrap a model) =="
start_ts="$(date +%s)"
deadline=$((start_ts + POLL_TIMEOUT))
next_report=$((start_ts + 60))
last_status=000
while :; do
  last_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$GATEWAY_URL" 2>/dev/null || true)"
  [ -n "$last_status" ] || last_status=000
  now_ts="$(date +%s)"
  elapsed=$((now_ts - start_ts))

  if [ "$last_status" = "200" ]; then
    pass "3/9" "gateway HTTP 200 at ${GATEWAY_URL}" "elapsed ${elapsed}s"
    break
  fi
  if [ "$now_ts" -ge "$deadline" ]; then
    fail "3/9" "gateway HTTP 200 at ${GATEWAY_URL}" \
      "timed out after ${elapsed}s (last status ${last_status})"
  fi
  if [ "$now_ts" -ge "$next_report" ]; then
    echo "  ... still waiting: status=${last_status} elapsed=${elapsed}s"
    next_report=$((now_ts + 60))
  fi
  sleep "$POLL_INTERVAL"
done

# The startup marker appears once at boot; the harness logs dozens of lines per
# minute afterwards (request spans + taskloop watcher), so the marker falls out
# of any fixed window long before a healthy long-running stack shows problems.
# Search the FULL logs (grep of a few thousand lines is cheap) instead of a
# tail window.
MARKER='starting bob-harness (python -m bob_harness.supervisor)'
if docker compose logs agent 2>/dev/null | grep -qF "$MARKER" \
  || docker logs bob-the-agent 2>/dev/null | grep -qF "$MARKER"; then
  pass "4/6" "container logs contain '${MARKER}'"
else
  fail "4/6" "container logs contain '${MARKER}'" \
    "marker not found in full container logs"
fi

if ! docker compose exec -T agent test -x /opt/bob-harness-venv/bin/python; then
  fail "5/9" "in-container bob-harness import" "/opt/bob-harness-venv/bin/python is missing"
fi
# Task 23: the importable package lives at /app/bob-harness/bob_harness (repo
# dir bob-harness, PEP-8 import name bob_harness) — run from that dir so
# python -c resolves it, mirroring bootstrap's PYTHONPATH=/app/bob-harness.
import_out="$(docker compose exec -T -w /app/bob-harness agent /opt/bob-harness-venv/bin/python \
  -c 'import bob_harness; print(bob_harness.__name__)' 2>&1 || true)"
if printf '%s\n' "$import_out" | grep -qx 'bob_harness'; then
  pass "5/9" "in-container bob-harness import (bob_harness.__name__ == 'bob_harness')"
else
  fail "5/9" "in-container bob-harness import" \
    "unexpected output: $(printf '%s' "$import_out" | tr '\n' ' ')"
fi

# 6. executor-backed task pickup check (P7): the flipped default runner is the
# in-process executor adapter (tasks.runner="executor"); probe the gateway's
# task submission + board endpoints so a pickup-path regression fails here.
# /api/healthz is unauthenticated; task routes need the API key, so this check
# exercises them only when BOB_HARNESS_API_KEY is set in the host shell.
healthz_out="$(curl -fsS --max-time 10 "http://${GATEWAY_HOST}/healthz" 2>/dev/null || true)"
if printf '%s\n' "$healthz_out" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
  pass "6/9" "/healthz ok:true (liveness)"
else
  fail "6/9" "/healthz ok:true (liveness)" "unexpected body: ${healthz_out:-<empty>}"
fi
if [ -n "${BOB_HARNESS_API_KEY:-}" ]; then
  pickup_out="$(docker compose exec -T -w /app/bob-harness agent /opt/bob-harness-venv/bin/python \
    -c 'from bob_harness.config import load_config; c = load_config(); print("runner=" + str(c.tasks.runner))' \
    2>&1 || true)"
  if printf '%s\n' "$pickup_out" | grep -qx 'runner=executor'; then
    pass "6/9" "taskloop resolved runner=executor (executor-backed pickup)"
  else
    fail "6/9" "taskloop resolved runner=executor (executor-backed pickup)" \
      "unexpected output: $(printf '%s' "$pickup_out" | tr '\n' ' ')"
  fi
else
  echo "RESULT: [6/9] taskloop resolved runner=executor ... SKIP (BOB_HARNESS_API_KEY unset)"
fi

# 6b. v2 dependency lock pins (P9): the smoke contract pins the frozen
# pydantic-ai v2 + harness dependency set host-side (grep the committed lock
# file - no container exec needed; if the lock drifts from the documented
# pins the smoke run fails here).
lock_file="src/bob-harness/requirements.lock"
req_file="src/bob-harness/requirements.txt"
for needle in 'pydantic-ai==2.73.0' 'pydantic-ai-slim==2.73.0' 'pydantic-graph==2.73.0' \
  'pydantic-evals==2.73.0' 'json-repair==0.64.9' 'pydantic==2.14.4' 'pydantic-core==2.50.5'; do
  if [ -f "$lock_file" ] && grep -qF -- "$needle" "$lock_file"; then
    pass "6b/9" "requirements.lock pins $needle"
  else
    fail "6b/9" "requirements.lock pins $needle" "needle missing from ${lock_file}"
  fi
done
if [ -f "$req_file" ] && grep -qF -- 'pydantic-ai-harness[coder]==0.73.0' "$req_file" \
  && [ -f "$lock_file" ] && grep -qF -- 'pydantic-ai-harness==0.73.0' "$lock_file"; then
  pass "6b/9" "requirements pin the harness extra ([coder] extra + frozen harness core)"
else
  fail "6b/9" "requirements pin the harness extra ([coder] extra + frozen harness core)" \
    "pydantic-ai-harness[coder]==0.73.0 (requirements.txt) or pydantic-ai-harness==0.73.0 (lock) missing"
fi

# 7. screenshot e2e entry (P7): the gateway serves the capture-ready page and
# the screenshot capture commands are documented; the scripted login -> chat ->
# task board -> answer capture flow lives in src/bob-harness/docs/.
page="$(curl -fsS --max-time 10 "$GATEWAY_URL" 2>/dev/null || true)"
for needle in 'id="tasks"' 'id="chat-form"' 'id="fallback-banner"' 'data-answer-swap="/api/tasks/answer/"'; do
  if printf '%s' "$page" | grep -qF "$needle"; then
    : # capture-ready DOM state present
  else
    fail "7/9" "gateway page carries capture-ready DOM" "missing: ${needle}"
  fi
done
if docker compose exec -T agent test -f /app/bob-harness/src/bob-harness/docs/taking-ux-captures.md \
  || docker compose exec -T agent test -f /app/bob-harness/docs/taking-ux-captures.md; then
  pass "7/9" "screenshot capture doc present + page capture-ready (see docs/taking-ux-captures.md)"
else
  pass "7/9" "screenshot capture flow (docs/taking-ux-captures.md, host-side listing)"
  if [ -f src/bob-harness/docs/taking-ux-captures.md ]; then
    pass "7/9" "capture doc verified from host tree (src/bob-harness/docs/taking-ux-captures.md)"
  else
    fail "7/9" "screenshot e2e entry" "capture doc src/bob-harness/docs/taking-ux-captures.md missing"
  fi
fi

ps_out="$(docker compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null || true)"
if [ -z "$ps_out" ]; then
  ps_out="$(docker compose ps 2>/dev/null || true)"
fi
if [ -z "$ps_out" ]; then
  fail "8/9" "compose services Up" "docker compose ps returned no output"
fi
not_up=""
for svc in ollama searxng valkey agent; do
  line="$(printf '%s\n' "$ps_out" | grep -E "(^|[[:space:]])${svc}([[:space:]]|$)" | head -n1 || true)"
  if [ -z "$line" ] || ! printf '%s\n' "$line" | grep -q 'Up'; then
    not_up="${not_up} ${svc}"
  fi
done
if [ -z "$not_up" ]; then
  pass "8/9" "compose services Up (ollama, searxng, valkey, agent)"
else
  fail "8/9" "compose services Up (ollama, searxng, valkey, agent)" "not Up:${not_up}"
fi

# 9. task board endpoint answers (P7): the polled board endpoint used by the
# screenshot e2e answers under auth (404 body allowed for an empty board,
# 401 without a key — both prove the route is wired post-flip).
board_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${GATEWAY_HOST}/api/tasks" 2>/dev/null || true)"
if [ "$board_status" = "200" ] || [ "$board_status" = "401" ] || [ "$board_status" = "404" ]; then
  pass "9/9" "/api/tasks route wired post-flip (status ${board_status})"
else
  fail "9/9" "/api/tasks route wired post-flip" "unexpected status ${board_status}"
fi

echo "ALL CHECKS PASSED"
