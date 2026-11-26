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
## Run 2026-11-24 (task 23 bob-harness rename + folder split, image rebuilt from poc)

- setup: fresh volume (`./volumes/agent` wiped — no migration, user decision); `docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .` from poc @ docs commit; `BOB_HARNESS_API_KEY` supplied via host env for the verification run (old `.env` still has `HARNESS_*` keys — user must rename them; never read/modified here).
- pre-composition checks: docker one-off pytest `-m 'not smoke'` → 463 passed; `npm test` → 214 passed / 8 skipped (docker-gated only).
- live e2e beyond the script's 6 checks (same boot):
  - `/opt/data` layout: exactly `bob-harness/`, `hermes/`, `opencode/` (+ base-image `lazy-packages/`); NO `/opt/data/{harness,profiles,.config,.local,.cache}` remnants; `hermes/profiles/{researcher,simple}` provisioned; `opencode/opencode.jsonc` at the pinned path.
  - container env: 7× `BOB_HARNESS_*`, `HERMES_HOME=/opt/data/hermes`, `OPENCODE_CONFIG=/opt/data/opencode/opencode.jsonc`, 4× `XDG_*` under `/opt/data/opencode/`.
  - hermes: direct `docker exec ... hermes -p simple chat --oneshot` exit 0 (4s, title `pong`); session rows written under `/opt/data/hermes/profiles/simple/state.db`.
  - gateway→hermes: `POST /api/chat` routed `agent=hermes profile=simple`, session source=`harness` completed `cli_close`, ledger row `exit_code: 0` (`duration_s` 6.9) at `/opt/data/bob-harness/runs_ledger.jsonl`.
  - opencode: `opencode run ... --dir /app/projects/oc-verify` exit 0, artifact written; state landed under `/opt/data/opencode/{data,state,config}/opencode` — `/opt/data/.local/share/opencode` and `/opt/data/.config/opencode` do NOT exist; nothing leaked into `/opt/data/hermes`. XDG redirection verified even from a plain `docker exec` (container-env XDG wins over HOME defaults).
  - discord: bot connected as `BOB-dev#3706` via `bob_harness.gateway.discord` (home channel unset on this keyless run).
- notes: (1) macOS virtiofs bind-mounts display all `/opt/data` files as `root:root` — ownership is created by bootstrap as the `hermes` user on real Linux hosts; write access tested OK. (2) smoke check 5 needed `-w /app/bob-harness`: the old `import harness` accidentally relied on CWD resolving the `/app/harness` package dir; the renamed package lives at `/app/bob-harness/bob_harness`.

| check | expected | actual | PASS/FAIL | date |
|-------|----------|--------|-----------|------|
| 1. build | image build succeeds (`docker build -f dockerfiles/Dockerfile.hermes .`) | built from clean checkout | PASS | 2026-11-24 |
| 2. up | `docker compose up -d` starts the stack | all services started (fresh volume) | PASS | 2026-11-24 |
| 3. gateway | `http://127.0.0.1:8181/` returns HTTP 200 within ~7050s | HTTP 200 (~40s incl. bootstrap provisioning) | PASS | 2026-11-24 |
| 4. startup marker | container logs contain `starting bob-harness (python -m bob_harness.supervisor)` | found | PASS | 2026-11-24 |
| 5. harness import | `-w /app/bob-harness` + `/opt/bob-harness-venv/bin/python -c 'import bob_harness; print(bob_harness.__name__)'` prints `bob_harness` | `bob_harness` (first run without `-w` failed — check fixed, see notes) | PASS | 2026-11-24 |
| 6. compose ps | all four services Up: `ollama`, `searxng`, `valkey`, `agent` | all Up, agent healthy | PASS | 2026-11-24 |

## Run 2026-11-26 (independent post-task-23 verification of running stack + smoke tooling fixes)

- trigger: independent re-verification of the task-23 deliverables on the live stack (fresh volume, image built ~14 min before run) — not a rebuild task. `docker build` + full host smoke + in-container pytest + host jest + live agent e2e.
- verification results (task-23 layout confirmed working):
  - `/opt/data` layout: exactly `bob-harness/`, `hermes/`, `opencode/` (+ base-image `lazy-packages/`); no `/opt/data/{harness,profiles,.config,.local,.cache}` remnants.
  - container env: 7× `BOB_HARNESS_*`, `HERMES_HOME=/opt/data/hermes`, `OPENCODE_CONFIG`, 4× `XDG_*`; zero `HARNESS_*` vars.
  - hermes: `hermes -p simple chat --oneshot` exits 0, replies `pong`.
  - gateway→hermes: `POST /api/chat` → routed `agent=hermes profile=simple` → `agent_final` → `idle`; repeated across a container recreate (exit_code 0 rows before and after).
  - opencode: `opencode run` writes artifact to `/app/projects/oc-verify`; state under `/opt/data/opencode/{data,state}/opencode`; `/opt/data/.local/share/opencode` does not exist.
  - pytest in container: 463 passed / 3 deselected (both script invocation modes); host jest: 214 passed / 8 skipped.
- defects found and fixed in this run (the harness itself was correct):
  1. `scripts/host-smoke.sh` check 4/6 grepped only `tail -50` of the logs — the startup marker falls out of a 50-line window within minutes on a healthy chatty stack (first run above FAILED 4/6). Now greps the FULL compose/docker logs.
  2. `scripts/host-smoke.sh` check 1/6 ran `docker compose build`, which is a no-op (compose has no `build:` section — image pinned at `bob-the-agent:latest`), so smoke on a fresh host would verify nothing and fail confusingly later. Now runs the documented `docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .`.
  3. `scripts/dev-incontainer-tests.sh` derived the source dir from the script's own location (`scripts/../src/bob-harness`) — host-only layout; in the container the code is at `/app/bob-harness` (also crashed under `bash -s` because `BASH_SOURCE` is empty on stdin). Now detects the in-container layout and falls back to `$0`.
  4. No `.dockerignore` — `COPY ./src/bob-harness/ /app/bob-harness/` baked the workspace's macOS `.venv` (symlinks to `/opt/homebrew/...`), `.pytest_cache` and `__pycache__` into the image; runtime unaffected (`/opt/bob-harness-venv`), but `/app/bob-harness/.venv/bin/python` was a dead interpreter. Root `.dockerignore` added (dev venvs/caches + `.env`*); verified the rebuilt image is clean. NOTE: image must be rebuilt for this to take effect on other hosts.
- re-run after fixes: full host smoke 6/6 PASS (incl. real build); pytest 463 passed; post-rebuild gateway→hermes chat exit_code 0.

| check | expected | actual | PASS/FAIL | date |
|-------|----------|--------|-----------|------|
| 1. build | image build succeeds (`docker build -f dockerfiles/Dockerfile.hermes .`) | PASS (first run: compose no-op — fixed) | PASS | 2026-11-26 |
| 2. up | `docker compose up -d` | all services started/recreated | PASS | 2026-11-26 |
| 3. gateway | HTTP 200 at `http://127.0.0.1:8181/` | 200 (30s after recreate) | PASS | 2026-11-26 |
| 4. startup marker | full logs contain `starting bob-harness (python -m bob_harness.supervisor)` | found (first run: tail-50 window miss — fixed) | PASS | 2026-11-26 |
| 5. harness import | `-w /app/bob-harness` import prints `bob_harness` | `bob_harness` | PASS | 2026-11-26 |
| 6. compose ps | ollama, searxng, valkey, agent all Up | all Up, agent healthy | PASS | 2026-11-26 |
