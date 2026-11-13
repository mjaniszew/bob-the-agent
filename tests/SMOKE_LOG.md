# Host smoke results

Results log template for [`scripts/host-smoke.sh`](../scripts/host-smoke.sh). Run the script on the **Docker HOST** (not inside a container); fill in the `actual`, `PASS/FAIL`, and `date` columns for each check after a run.

| check | expected | actual | PASS/FAIL | date |
|-------|----------|--------|-----------|------|
| 1. build | `docker compose build` succeeds | | | |
| 2. up | `docker compose up -d` starts the stack | | | |
| 3. gateway | `http://127.0.0.1:8181/` returns HTTP 200 within ~7050s | | | |
| 4. startup marker | container logs (last 50 lines) contain `starting harness (supervisord)` | | | |
| 5. harness import | `/opt/harness-venv/bin/python -c 'import harness; print(harness.__name__)'` prints `harness` | | | |
| 6. compose ps | all four services Up: `ollama`, `searxng`, `valkey`, `agent` | | | |
