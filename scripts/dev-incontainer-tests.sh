#!/usr/bin/env bash
# In-container unit/contract tests for the harness PoC (branch poc).
# Canonical pytest invocation: cd src/harness && pytest (pyproject pythonpath=["."]).
# Creates the scratch venv at /tmp/harness-venv if missing; installs requirements
# files (needs PyPI reachability ONCE) ; test execution itself stays offline.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/src/harness"
VENV=/tmp/harness-venv
if [ ! -x "$VENV/bin/python" ]; then
  echo "== creating venv at $VENV =="
  uv venv "$VENV" --python 3.13
fi
echo "== installing runtime requirements (needs PyPI; idempotent) =="
uv pip install --python "$VENV/bin/python" -r requirements.txt -q
echo "== installing dev tools (excluded from requirements.lock by design) =="
uv pip install --python "$VENV/bin/python" -r requirements-dev.txt -q
echo "== running pytest (-m \"not smoke\"; canonical form per IMPLEMENTATION-PLAN.md R1/M4) =="
cd "$repo_root/src/harness"
exec /tmp/harness-venv/bin/python -m pytest -m "not smoke" "$@"
