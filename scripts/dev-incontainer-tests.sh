#!/usr/bin/env bash
# In-container unit/contract tests for the bob-harness PoC (branch poc).
# Canonical pytest invocation: cd src/bob-harness && pytest
# (pyproject pythonpath=["."]).
# Creates the scratch venv at /tmp/bob-harness-venv if missing; installs
# requirements files (needs PyPI reachability ONCE); test execution itself
# stays offline.
# XDG_CACHE_HOME is overridden to /tmp so the uv cache cannot land under
# /opt/data/opencode/cache (compose pins the container-level XDG roots for
# opencode; the test venv must not share them).
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/src/bob-harness"
VENV=/tmp/bob-harness-venv
export XDG_CACHE_HOME=/tmp/bob-harness-cache
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/tmp/bob-harness-cache/config}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "== creating venv at $VENV =="
  uv venv "$VENV" --python 3.13
fi
echo "== installing runtime requirements (needs PyPI; idempotent) =="
uv pip install --python "$VENV/bin/python" -r requirements.txt -q
echo "== installing dev tools (excluded from requirements.lock by design) =="
uv pip install --python "$VENV/bin/python" -r requirements-dev.txt -q
echo "== running pytest (-m \"not smoke\"; canonical form per IMPLEMENTATION-PLAN.md R1/M4) =="
cd "$repo_root/src/bob-harness"
exec /tmp/bob-harness-venv/bin/python -m pytest -m "not smoke" "$@"
