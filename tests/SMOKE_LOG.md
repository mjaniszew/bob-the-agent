# Host smoke results

Results log template for [`scripts/host-smoke.sh`](../scripts/host-smoke.sh). Run the script on the **Docker HOST** (not inside a container); fill in the `actual`, `PASS/FAIL`, and `date` columns for each run.

## Run 2026-11-23 (task 22 fixes, image rebuilt from poc @ watcher-survives fix)

- invocation: `scripts/host-smoke.sh --skip-build` (image built just before with `docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .`)
- live e2e beyond the script's 6 checks (same boot): `POST /api/chat` routed hermes `-p simple`, reply PROFILE_OK, terminal `final`, exit_code 0 in `runs_ledger.jsonl`; task-inbox `OCDE2236` (opencode, workdir /app/projects/oc-verify) reached DONE with the artifact written; legacy hermes gateway NOT started (cont-init `02-harness-only` clears `gateway_state.json`); Discord bot connected (single identity).

| check | expected | actual | PASS/FAIL | date |
|-------|----------|--------|-----------|------|
| 1. build | `docker compose build` succeeds (compose has no build section — image built via README command) | `docker compose build` no-op + image built separately | PASS | 2026-11-23 |
| 2. up | `docker compose up -d` starts the stack | all services started | PASS | 2026-11-23 |
| 3. gateway | `http://127.0.0.1:8181/` returns HTTP 200 within ~7050s | HTTP 200 at 0s | PASS | 2026-11-23 |
| 4. startup marker | container logs (last 50 lines) contain `starting harness (python -m harness.supervisor)` | found | PASS | 2026-11-23 |
| 5. harness import | `/opt/harness-venv/bin/python -c 'import harness; print(harness.__name__)'` prints `harness` | `harness` | PASS | 2026-11-23 |
| 6. compose ps | all four services Up: `ollama`, `searxng`, `valkey`, `agent` | all Up, agent healthy | PASS | 2026-11-23 |
