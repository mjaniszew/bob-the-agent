#!/usr/bin/env bash
# host-smoke.sh — smoke-verify the bob-the-agent compose stack from the Docker HOST.
#
# Runs on the user's Docker HOST (NOT inside a container). Requires Docker and
# docker compose v2. Verifies the harness gateway on 127.0.0.1:8181 end to end:
# image build, stack start, gateway HTTP 200, startup marker in container logs,
# in-container harness import, and all four compose services Up.
#
# Usage: ./scripts/host-smoke.sh [--skip-build]
#
# Security: this script never reads or prints .env contents and never echoes
# secret values. Variable NAMES are reported only as <set>/<unset>.
set -euo pipefail

SKIP_BUILD=0
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
for var in HARNESS_API_KEY DISCORD_BOT_TOKEN; do
  if [ -n "${!var:-}" ]; then
    echo "using ${var}=<set>"
  else
    echo "using ${var}=<unset in host shell; compose may load it from .env>"
  fi
done

if [ "$SKIP_BUILD" -eq 1 ]; then
  echo "RESULT: [1/6] docker compose build ... SKIP (--skip-build)"
elif docker compose build; then
  pass "1/6" "docker compose build"
else
  fail "1/6" "docker compose build" "build failed"
fi

if docker compose up -d; then
  pass "2/6" "docker compose up -d"
else
  fail "2/6" "docker compose up -d" "compose up failed"
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
    pass "3/6" "gateway HTTP 200 at ${GATEWAY_URL}" "elapsed ${elapsed}s"
    break
  fi
  if [ "$now_ts" -ge "$deadline" ]; then
    fail "3/6" "gateway HTTP 200 at ${GATEWAY_URL}" \
      "timed out after ${elapsed}s (last status ${last_status})"
  fi
  if [ "$now_ts" -ge "$next_report" ]; then
    echo "  ... still waiting: status=${last_status} elapsed=${elapsed}s"
    next_report=$((now_ts + 60))
  fi
  sleep "$POLL_INTERVAL"
done

logs="$(docker compose logs agent 2>/dev/null | tail -50 || true)"
if ! printf '%s\n' "$logs" | grep -qF 'starting harness (supervisord)'; then
  logs="$(docker logs bob-the-agent 2>/dev/null | tail -50 || true)"
fi
if printf '%s\n' "$logs" | grep -qF 'starting harness (supervisord)'; then
  pass "4/6" "container logs contain 'starting harness (supervisord)'"
else
  fail "4/6" "container logs contain 'starting harness (supervisord)'" \
    "marker not found in the last 50 log lines"
fi

if ! docker compose exec -T agent test -x /opt/harness-venv/bin/python; then
  fail "5/6" "in-container harness import" "/opt/harness-venv/bin/python is missing"
fi
import_out="$(docker compose exec -T agent /opt/harness-venv/bin/python \
  -c 'import harness; print(harness.__name__)' 2>&1 || true)"
if printf '%s\n' "$import_out" | grep -qx 'harness'; then
  pass "5/6" "in-container harness import (harness.__name__ == 'harness')"
else
  fail "5/6" "in-container harness import" \
    "unexpected output: $(printf '%s' "$import_out" | tr '\n' ' ')"
fi

ps_out="$(docker compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null || true)"
if [ -z "$ps_out" ]; then
  ps_out="$(docker compose ps 2>/dev/null || true)"
fi
if [ -z "$ps_out" ]; then
  fail "6/6" "compose services Up" "docker compose ps returned no output"
fi
not_up=""
for svc in ollama searxng valkey agent; do
  line="$(printf '%s\n' "$ps_out" | grep -E "(^|[[:space:]])${svc}([[:space:]]|$)" | head -n1 || true)"
  if [ -z "$line" ] || ! printf '%s\n' "$line" | grep -q 'Up'; then
    not_up="${not_up} ${svc}"
  fi
done
if [ -z "$not_up" ]; then
  pass "6/6" "compose services Up (ollama, searxng, valkey, agent)"
else
  fail "6/6" "compose services Up (ollama, searxng, valkey, agent)" "not Up:${not_up}"
fi

echo "ALL CHECKS PASSED"
