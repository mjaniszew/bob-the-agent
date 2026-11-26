#!/usr/bin/env bash
# In-container unit/contract tests for the bob-harness PoC (branch poc).
# Canonical pytest invocation: cd (repo src dir) && pytest
# (pyproject pythonpath=["."]).
#
# The container layout (task 23) has no src/ tree: the code lives at
# /app/bob-harness, the script at /app/scripts/. Detect it instead of
# deriving the source dir from the script's location, which only matches
# the host checkout. Run it either way:
#   docker exec -i bob-the-agent bash -s < scripts/dev-incontainer-tests.sh   (stdin)
#   docker cp scripts/dev-incontainer-tests.sh bob-the-agent:/app/scripts/ \
#     && docker exec bob-the-agent bash /app/scripts/dev-incontainer-tests.sh (copied)
# Creates the scratch venv at /tmp/bob-harness-venv if missing; installs
# requirements files (needs PyPI reachability ONCE); test execution itself
# stays offline.
# XDG_CACHE_HOME is overridden to /tmp so the uv cache cannot land under
# /opt/data/opencode/cache (compose pins the container-level XDG roots for
# opencode; the test venv must not share them).
set -euo pipefail
# BASH_SOURCE is empty under `bash -s < script` — fall back to $0. The dirname
# is only used for the host-layout branch; the in-container branch ignores it.
if [ -d /app/bob-harness ]; then
  SRC_DIR=/app/bob-harness
else
  script_src="${BASH_SOURCE[0]:-$0}"
  repo_root="$(cd "$(dirname "${script_src}")/.." && pwd)"
  SRC_DIR="$repo_root/src/bob-harness"
fi
cd "$SRC_DIR"
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
cd "$SRC_DIR"
exec /tmp/bob-harness-venv/bin/python -m pytest -m "not smoke" "$@"
