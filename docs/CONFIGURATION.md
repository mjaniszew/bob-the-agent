# Configuration Guide

## Overview

Bob The Agent uses environment variables and YAML configuration files for customization. The system is built on the Hermes Agent framework with a single-container architecture: one `agent` container runs the Python bob-harness (the orchestrator: FastAPI bob-harness gateway on 127.0.0.1:8181, pydantic-AI router, Discord bot, task loop, scheduler) which delegates to the two Hermes profiles (`researcher`, `simple`) and OpenCode CLI via one-shot subprocesses (no coder profile; no legacy hermes gateway).

## Configuration Files

### .env

Main configuration file for environment variables.

[.env.template](/.env.template) contains possible environment variables with explanations.

### hermes.template.yaml

Shared Hermes Agent behavior configuration template located at `src/config/hermes.template.yaml`.

**How it works** (at container startup, in `bootstrap.sh`):
1. For each secondary profile (`researcher`, `simple`) at `/opt/data/hermes/profiles/{name}/config.yaml`:
   - If the config does not exist, `generate-config.sh` reads the YAML template and the profile's partial
   - If a partial exists for the profile, `merge-yaml.mjs` deep-merges them (arrays replaced, objects merged, partial wins)
   - If no partial exists, the template is used as-is
   - If the config already exists (e.g. from a previous boot or a migrated install), it is **reconciled** with the current partial instead — deep-merged again (partial wins, profile-only keys preserved) after a one-time `config.yaml.pre-reconcile.bak` snapshot
2. Final config is written to the profile's `config.yaml`

The template uses Hermes Agent's native YAML format. It contains defaults for model settings, toolsets, agent behavior, terminal configuration, browser settings, checkpoints, and more.

**Example hermes.template.yaml structure:**

```yaml
model:
  default: glm-5.3-flash:cloud
  provider: custom
  base_url: http://ollama:11434/v1
providers: {}
toolsets:
  - hermes-cli
agent:
  max_turns: 90
  gateway_timeout: 2700
  personalities:
    default: You are automation agent...
terminal:
  backend: local
  persistent_shell: true
  lifetime_seconds: 300
approvals:
  mode: off
gateway:
  multiplex_profiles: true
```

### Profile Partials (hermes.partial.yml)

Each profile overrides specific parts of the shared template via a partial YAML file in `src/agents/{name}/hermes.partial.yml`:

**Researcher profile** (`src/agents/researcher/hermes.partial.yml`):
```yaml
model:
  default: glm-5.3:cloud
  provider: custom
  base_url: http://ollama:11434/v1
  # Per-request ollama context (see the known-tension note under Runtime knobs).
  ollama_num_ctx: 256000
custom_providers:
  - name: ollama/glm-5.3:cloud
    base_url: http://ollama:11434/v1
    model: glm-5.3:cloud
toolsets:
  - hermes-cli
  - browser
agent:
  gateway_timeout: 2700
  api_max_retries: 5
  reasoning_effort: medium
  local_stream_stale_timeout: 3600
```

(`gateway.multiplex_profiles: true` is not set here — it lives in `src/config/hermes.template.yaml`, lines 134-135, and applies globally to the single gateway.)

**Researcher / simple profiles** (`src/agents/researcher|simple/hermes.partial.yml`): same wiring pattern and runtime knobs (`ollama_num_ctx`, `reasoning_effort`, `local_stream_stale_timeout`) — researcher uses `glm-5.3:cloud` with `ollama_num_ctx: 256000`; simple uses `glm-5.3-flash:cloud` with `ollama_num_ctx: 65536`. Note `agent.gateway_timeout` and `agent.api_max_retries` differ per profile: researcher 2700/5, simple 1800/5.

**Coder profile removed:** The coder Hermes profile was removed in the bob-harness PoC final phase. bob-harness invokes OpenCode directly; the researcher and simple profiles remain. See `tests/coder-agent.test.ts`, which now asserts the deletion (`src/agents/coder` must not reappear).

### Runtime knobs worth knowing

| Key | Default here | Purpose |
|-----|--------------|---------|
| `model.ollama_num_ctx` | `256000` (researcher), `65536` (simple) | Per-request ollama context window for the GLM cloud models (GLM context limit 512000). Known tension: these pins exceed the ollama container's `OLLAMA_CONTEXT_LENGTH` compose default (`65536`) — both are the real values as shown here; keep the relation in mind when tuning. |
| `agent.reasoning_effort` | `medium` (template default (src/config/hermes.template.yaml:29); partials pin `medium` for `researcher` and `low` for `simple`) | Reasoning effort passed to the model provider. |
| `agent.local_stream_stale_timeout` | `3600` | Hermes kills model connections that receive no chunks for 900s by default; on slow CPU inference a long prefill looks exactly like a stalled stream. Raised to 3600s so big-prompt turns survive prefill. |
| `approvals.mode` | `off` | Full approval bypass across interactive, gateway, cron and unattended contexts — required for 24/7 unattended operation. Replaces the old auto-approve env var, denylisted upstream in Hermes v0.21. Valid modes: `manual | smart | off`. |
| `gateway.multiplex_profiles` | `true` (set in `src/config/hermes.template.yaml`) | Makes the single gateway serve all profiles. Set once in the shared template, so it applies globally to the one gateway — not per-profile in a partial. |

**Merge behavior:**
- Objects are deep-merged (nested keys override individually)
- Arrays are replaced entirely (not concatenated)
- Keys present only in the partial are added
- Keys present only in the template (or in an existing config) are preserved

### Per-profile .env

Bootstrap writes a `.env` (chmod 600) into each secondary profile directory (`/opt/data/hermes/profiles/{name}/.env`) carrying the profile's secret scope (Ollama/SearXNG URLs, x-com/grok/AWS S3 keys). Hermes never inherits the root `.env` into profiles.

### Harness config file (bob-harness.template.yaml)

The Python bob-harness has its **own** YAML config, separate from the Hermes templates above:

- **Template:** `src/bob-harness/bob-harness.template.yaml` (server bind port 8181, `logfire`, `api_key`, `rate_limit`, `router`, `agents.hermes.default_profile`, scheduler jobs)
- **Seeding:** `bootstrap.sh` copies it once to `/opt/data/bob-harness/config.yaml` (`[ -f ... ] || cp` — a boot that died mid-seed reruns safely; user edits on the volume are never overwritten)
- **Resolution:** compose sets `BOB_HARNESS_CONFIG=/opt/data/bob-harness/config.yaml` so `load_config()` reads the seeded file. Without it the harness looks for `./config.yaml` in the CWD (`/app`), finds none, and silently falls back to built-in defaults — YAML-only settings are discarded. (Regression fixed in commit `ec2dd44`; `tests/harness-contracts.test.ts` asserts the seven `BOB_HARNESS_*` keys compose exposes, including `BOB_HARNESS_CONFIG`.)
- **Precedence:** flat `BOB_HARNESS_*` env vars override the YAML values; everything else comes from the file
- **Schema:** parsed into pydantic models in `src/bob-harness/harness/config.py` (`HarnessConfig`, incl. `LogfireConfig`; unknown keys are rejected with `extra="forbid"`)

### Agent Identity Files

Each profile directory carries exactly two files under `src/agents/{name}/`. `SOUL.md` is copied into the profile's workspace at startup; the partial is merged into the profile's `config.yaml` by `generate-config.sh` (see [Configuration Files](#configuration-files)):

| File | Purpose |
|------|---------|
| `src/agents/{name}/SOUL.md` | Agent soul/persona markdown (personality and behavioral instructions) |
| `src/agents/{name}/hermes.partial.yml` | Profile config partial overlay, deep-merged over `src/config/hermes.template.yaml` |

### OpenCode Configuration (Coding Layer)

OpenCode CLI is the coding engine invoked directly by the bob-harness (it is not a Hermes profile), configured via a template that is environment-substituted at container startup. OpenCode CLI itself is installed at image build time with the deliberate pin `opencode-ai@1.18.32`.

**Template file:** `src/config/opencode.template.jsonc`

At startup, `bootstrap.sh` generates the OpenCode config by substituting the template's placeholder:

| Placeholder | Replaced With |
|-------------|---------------|
| `OLLAMA_BASE_URL_PLACEHOLDER` | Value of `OLLAMA_BASE_URL` env var |

**Generated config location:** `/opt/data/opencode/opencode.jsonc` (pinned by the `OPENCODE_CONFIG` compose env var)

The OpenCode config defines (see `src/config/opencode.template.jsonc` for the full template):
- **Provider**: Ollama via the OpenAI-compatible endpoint, using GLM cloud models (GLM limits: context 512000, output 32000)
- **Agents**: `build` (primary: `glm-5.3-flash:cloud`) and `plan` (primary: `glm-5.3:cloud`); subagents `general`, `explore`, and `scout` (`glm-5.3-flash:cloud`)
- **code-reviewer**: subagent with primary `kimi-k2.7-code:cloud`, read-only (`write`/`edit` tools disabled), tuned for security/performance/maintainability review

## Environment Variables

### Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | string | `info` | Logging level (debug, info, warn, error) |
| `NODE_ENV` | string | `production` | Node environment |
| `OLLAMA_CONTEXT_LENGTH` | number | `65536` | Ollama server-side context length (compose default `65536`). Known tension: the researcher profile partial pins `ollama_num_ctx: 256000` (simple pins `65536`) — above this compose default. Both values are real; consider both when tuning. |
| `HERMES_API_TIMEOUT` | number | `3600` | Hermes API read timeout in seconds, raised above Hermes' 900s default so long-running turns survive. |
| `HERMES_UID` / `HERMES_GID` | number | - | Linux hosts: run the container with these UID/GID so the `./volumes/*` bind mounts are owned by your user |
| `OPENCODE_CONFIG` | path | `/opt/data/opencode/opencode.jsonc` | Where bootstrap writes the generated OpenCode config |
| `USER_PROJECTS_FOLDER` | string | - | Host-side source override for the `/app/projects` bind mount |
| `BOB_HARNESS_DISCORD_HOME_CHANNEL` | string | - | Discord home channel (numeric id) for harness notices with no originating thread (task state changes, gate questions); fallback env `DISCORD_HOME_CHANNEL`; unset = notices dropped with a rate-limited log |
| `DISCORD_ALLOWED_USERS` / `DISCORD_ALLOWED_ROLES` | string | - | Optional comma/space-separated allowlists (user/role ids); unset/empty = allow-all (PoC single-user default) |

### bob-harness (python orchestrator)

These come from compose (`compose.yaml`) — `BOB_HARNESS_*` binds/overrides only; anything else stays YAML-only (see [Harness config file](#harness-config-file-harnesstemplateyaml)):

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BOB_HARNESS_API_KEY` | string | - | Required; API key securing the harness gateway (`X-API-Key`); empty = fail-fast at boot (generate: `openssl rand -hex 32`) |
| `BOB_HARNESS_CONFIG` | path | `/opt/data/bob-harness/config.yaml` | In-container path of the harness YAML config (compose-fixed). Without it `load_config()` looks for `./config.yaml` in the CWD (`/app`), finds none, and **silently falls back to built-in defaults** — every YAML-only setting (`logfire`, `rate_limit`, scheduler jobs) is discarded |
| `BOB_HARNESS_ROUTER_BASE_URL` | string | `http://ollama:11434/v1` | OpenAI-compatible endpoint for the router brain (`base_url` bound from env; the `BOB_HARNESS_ROUTER_API_KEY` is deliberately NOT bound) |
| `BOB_HARNESS_ROUTER_MODEL` | string | `glm-5.3-flash:cloud` | Router-brain model |
| `BOB_HARNESS_DATA_DIR` | path | `/opt/data/bob-harness` | Harness state root (in-container; fixed by compose) |
| `BOB_HARNESS_WORKDIR` | path | `/app/projects` | Harness workdir root (in-container; fixed by compose) |
| `LOGFIRE_TOKEN` | string | - | Optional Logfire ingest token for bob-harness observability (see [Observability (Logfire)](#observability-logfire)); without a token telemetry is not exported to Logfire |

### Model Providers

| Variable | Type | Description |
|----------|------|-------------|
| `OLLAMA_BASE_URL` | string | Ollama API endpoint (defaults to http://ollama:11434) |

### Search & External Services

| Variable | Type | Description |
|----------|------|-------------|
| `SEARXNG_BASE_URL` | string | SearXNG URL (defaults to http://searxng:8888) |
| `USER_X_COM_API_TOKEN` | string | X.com API token for x-com skill |
| `USER_XAI_SEARCH_API_KEY` | string | xAI API key for grok-search skill |

Note: the skills that consume these variables are standalone — each carries its own dependency manifest (`package.json` / `requirements.txt`) inside `src/skills/<skill>/`, installed at Docker-build time; no runtime or configuration step prepares dependencies for skills.

### Discord Bot

| Variable | Type | Description |
|----------|------|-------------|
| `DISCORD_BOT_TOKEN` | string | Discord bot token |
| `DISCORD_ALLOWED_USERS` | string | User IDs allowed to use the bot; pairing approve writes approved IDs into this list |
| `DISCORD_ALLOWED_ROLES` | string | Role-ID allowlist for guild messages; DMs require user IDs in `DISCORD_ALLOWED_USERS` |

### AWS S3

| Variable | Type | Description |
|----------|------|-------------|
| `USER_AWS_S3_BUCKET` | string | S3 bucket name |
| `USER_AWS_S3_REGION` | string | S3 region (default: us-east-1) |
| `USER_AWS_S3_ACCESS_KEY_ID` | string | AWS access key |
| `USER_AWS_S3_SECRET_ACCESS_KEY` | string | AWS secret key |

## Docker Compose Configuration

### Service Architecture

The compose file defines 4 services. The two Hermes profiles (`researcher`, `simple`) live in the single `agent` service (image `bob-the-agent:latest`); bootstrap provisions those two profiles, seeds the harness state root, and execs the harness long-runner directly (task 22: `python -m bob_harness.supervisor` with `PYTHONPATH=/app/bob-harness` — the FastAPI harness gateway, API-key secured, listening on 0.0.0.0:8181; s6 stays PID 1, docker's `restart: unless-stopped` covers crashes; there is no supervisord layer and no legacy hermes gateway — a `02-bob-harness-only` cont-init hook clears the persisted `gateway_state.json` each boot so the base image never auto-starts it):

```yaml
services:
  ollama:          # LLM inference engine
  agent:           # Single container: Hermes profiles researcher+simple + bob-harness gateway (:8181) + OpenCode CLI
  searxng:         # Privacy-respecting metasearch engine
  valkey:          # Redis-compatible cache for SearXNG
```

**Sidecar image pins:** `searxng/searxng` is pinned via its `SEARXNG_VERSION` env var (`2026.4.29-cba0cffa8`); valkey runs the `valkey:9-alpine` image.

### Resource Limits

The `agent` service reserves 2 CPUs / 4G memory with 8G memory limits:

```yaml
agent:
  deploy:
    resources:
      reservations:
        cpus: 2
        memory: 4G
      limits:
        cpus: 2
        memory: 8G
```

### Volume Mounts

| Host Path | Container Path | Service | Purpose |
|-----------|---------------|---------|---------|
| `./volumes/agent` | `/opt/data` | agent | Single agent workspace: each agent state in its own folder under `/opt/data`: `bob-harness/` (bob-harness state), `hermes/` (HERMES_HOME incl. profiles/) — each agent state in its own folder: `bob-harness/`, `hermes/profiles/`, `opencode/` |
| `./volumes/results` | `/app/results` | agent | Final task output files |
| `./volumes/projects` | `/app/projects` | agent | Shared project workspace (git repos, code) |
| `ollama_data` | `/root/.ollama` | ollama | Downloaded models |
| `searxng_config` | `/etc/searxng/` | searxng | SearXNG configuration |
| `searxng_data` | `/var/cache/searxng/` | searxng | SearXNG cache |
| `valkey_data` | `/data` | valkey | Valkey/Redis data |

### Ports

| Host Port | Container Port | Service |
|-----------|---------------|---------|
| `8181` (loopback) | `8181` | agent — bob-harness gateway (published as 127.0.0.1:8181:8181 only) |
| `11434` | `11434` | Ollama API |
| `8888` | `8888` | SearXNG |

The agent healthcheck probes TCP 8181 via the shared `x-agent-healthcheck` anchor (compose.yaml lines 10-19; `start_period: 120s`), and the bob-harness gateway exposes an unauthenticated `GET /healthz` returning exactly `{"ok": true}` (all other routes are API-key secured).

<!-- append-anchor: P9 harness capability configuration -->

## Harness Capability Configuration (pydantic-ai v2, phases P2-P9)

The executor agent's pydantic-ai-harness capability stack is configured under
the top-level `harness:` YAML key of the bob-harness config
(`/opt/data/bob-harness/config.yaml`, template
`src/bob-harness/bob-harness.template.yaml`). Every sub-key has `extra="forbid"`
— unknown keys fail fast at boot — and a flat `BOB_HARNESS_*` env binding where
the table below shows one (list-valued keys are YAML-only by design; no flat
binding). Toggle keys default to ON; disabling a capability simply omits it
from the executor agent's `capabilities=[...]` and the executor still functions
(graceful degradation, asserted by tests).

### The harness group (bob-harness.template.yaml keys)

| Config key | Type | Default | Purpose |
|---|---|---|---|
| `harness.memory.enabled` | bool | `true` | Memory capability mounted on the executor |
| `harness.memory.dir` | str | `''` | Store dir; `''` -> `<storage.data_dir>/memory`, SHARED with MemoryEngine (D4) |
| `harness.memory.workspace_root` | str | `''` | `''` -> per-run workspace (task workdir / session workdir); LocalWorkspaceBackend binding |
| `harness.memory.agent_name` | str | `executor` | `Memory(store=..., agent_name=...)` |
| `harness.memory.inject` | bool | `true` | `inject_memory=True/False` |
| `harness.memory.max_tokens` | int | `2000` | Memory `max_tokens` (SDK default, verified) |
| `harness.skills.enabled` | bool | `true` | Skills capability mounted on the executor |
| `harness.skills.include` | list | `[]` | Optional slug filter (Skills `include=`) [YAML-only] |
| `harness.skills.exclude` | list | `[]` | Optional slug filter (Skills `exclude=`) [YAML-only] |
| `harness.skills.read_files` | bool | `false` | Skills `read_files` |
| `harness.skills.missing_directories` | `'error'\|'skip'` | `skip` | Fresh-tree boot uses `skip` (`ignore` raises ValueError — verified) |
| `harness.ask_user.enabled` | bool | `true` | AskUser capability mounted on the executor |
| `harness.ask_user.id` | str | `ask_user` | `AskUser(id=...)`; tool name `ask_user_question` |
| `harness.ask_user.answerer` | `'deferred'\|'inline'\|'none'` | `deferred` | `deferred` (default, D3): run parks via `DeferredToolRequests`, TaskLoop answers on resume. `inline`: interactive Answerer + gateway session/bus within `timeout_s`. `none`: no answering surface |
| `harness.ask_user.timeout_s` | int\|null | `null` | `AskUser(timeout=...)` when `answerer=inline` |
| `harness.subagents.enabled` | bool | `true` | Nested SubAgents capability (in-process helper entries only — NEVER hermes/opencode, D1) |
| `harness.subagents.max_depth` | int | `3` | `SubAgents(max_depth=...)` |
| `harness.delegation.enabled` | bool | `true` | `delegate_run` custom capability (D1) — hermes/opencode CLI adapters exposed as executor tools |
| `harness.delegation.tool_name` | str | `delegate_run` | Tool name of the custom capability [YAML-only] |

### Flat environment bindings (BOB_HARNESS_* table)

| Config key | Env var | Default |
|---|---|---|
| `harness.memory.enabled` | `BOB_HARNESS_MEMORY_ENABLED` | `true` |
| `harness.memory.dir` | `BOB_HARNESS_MEMORY_DIR` | `''` -> `<storage.data_dir>/memory` |
| `harness.memory.workspace_root` | `BOB_HARNESS_MEMORY_WORKSPACE` | `''` -> per-run workdir |
| `harness.memory.agent_name` | `BOB_HARNESS_MEMORY_AGENT_NAME` | `executor` |
| `harness.memory.inject` | `BOB_HARNESS_MEMORY_INJECT` | `true` |
| `harness.memory.max_tokens` | `BOB_HARNESS_MEMORY_MAX_TOKENS` | `2000` |
| `harness.skills.enabled` | `BOB_HARNESS_SKILLS_ENABLED` | `true` |
| `harness.skills.read_files` | `BOB_HARNESS_SKILLS_READ_FILES` | `false` |
| `harness.skills.missing_directories` | `BOB_HARNESS_SKILLS_MISSING` | `skip` |
| `harness.ask_user.enabled` | `BOB_HARNESS_ASK_USER_ENABLED` | `true` |
| `harness.ask_user.answerer` | `BOB_HARNESS_ASK_USER_ANSWERER` | `deferred` |
| `harness.ask_user.timeout_s` | `BOB_HARNESS_ASK_USER_TIMEOUT_S` | `null` |
| `harness.subagents.enabled` | `BOB_HARNESS_SUBAGENTS_ENABLED` | `true` |
| `harness.subagents.max_depth` | `BOB_HARNESS_SUBAGENTS_MAX_DEPTH` | `3` |
| `harness.delegation.enabled` | `BOB_HARNESS_DELEGATION_ENABLED` | `true` |
| `tasks.runner` | `BOB_HARNESS_TASKS_RUNNER` | `executor` |

Integer-valued env vars (`..._MAX_TOKENS`, `..._TIMEOUT_S`, `..._MAX_DEPTH`)
coerce exactly like `BOB_HARNESS_PORT`; boolean ones cast
`true/1/yes/on` -> true. `PYDANTIC_AI_NO_BANNER=1` is set in the
Dockerfile + compose (image/container env, not a `HarnessConfig` key).

### Task dispatch runner: `tasks.runner` (the P7 flip)

The config key `tasks.runner` (`analyzed | executor`) picks the adapter that
runs inbox tasks:

| Value | Behavior |
|---|---|
| `executor` (**executor default since P7**) | in-process pydantic-ai v2 agent + harness capabilities (Memory/Skills/AskUser/SubAgents/delegate_run) |
| `analyzed` | pre-migration behavior exactly: router-analyzed agent + profile -> hermes/opencode CLI adapter |

- **Default:** `executor` since phase P7 (single default literal, no state
  migration — merge-safe).
- **Rollback lever:** `BOB_HARNESS_TASKS_RUNNER=analyzed` restores 1.x behavior
  per environment without code changes (flat env overrides YAML per the
  precedence rules above).
- The runner value is resolved by taskloop from the same registry used by the
  gateway; see `src/bob-harness/bob_harness/config.py` (`TasksConfig.runner`)
  and the `scripts/host-smoke.sh` executor-pickup smoke check.

### v2 dependency pins (as frozen in requirements.lock at P0)

`pydantic-ai==2.73.0` · `pydantic-ai-slim==2.73.0` · `pydantic-graph==2.73.0` ·
`pydantic-evals==2.73.0` · `pydantic-ai-harness[coder]==0.73.0` ·
`json-repair==0.64.9` · `pydantic==2.14.4` · `pydantic-core==2.50.5`.
(`requirements.txt` carries the `>=2,<3` cap + the same harness extra;
`requirements.lock` is the frozen resolution, regenerated ONCE at P0 per
policy OQ8, dev-tools-free.)

<!-- append-anchor: P9 harness capability configuration end -->

## Model Configuration

### Changing Profile Models

Each profile's model is configured via its `hermes.partial.yml` file:

1. Edit the partial for the profile you want to change, e.g. `src/agents/researcher/hermes.partial.yml`:

```yaml
model:
  default: your-model-name:cloud
  provider: custom
  base_url: http://ollama:11434/v1
  ollama_num_ctx: 65536
custom_providers:
  - name: ollama/your-model-name:cloud
    base_url: http://ollama:11434/v1
    model: your-model-name:cloud
```

2. Restart the container. Existing profile configs are reconciled with the updated partial at boot (partial wins; a one-time `.pre-reconcile.bak` snapshot of the pre-reconcile config is taken):
```bash
docker compose up -d --force-recreate agent
```

### bob-harness Router + Profiles (task 22)

bob-harness delegates only via provisioned hermes profiles — `hermes -p <profile> chat --oneshot`, never profile-less (the default profile has no inference provider). The router LLM picks the profile per route (`simple` for quick chat/simple tasks, `researcher` for research/analysis); when it omits one, the config default `agents.hermes.default_profile` (`simple`) applies — set in `src/bob-harness/bob-harness.template.yaml` (hermes agents line) and overridable in `/opt/data/bob-harness/config.yaml`. The OpenCode adapter requires a workdir nested under `/app/projects`: task-inbox tasks carry their own `workdir`, chat/Discord/scheduled runs get `/app/projects/bob-harness-sessions/<session-id>` (auto-created).

### Adding Models to Ollama

1. Pull additional models:
   ```bash
   docker exec bob-the-agent-ollama ollama pull your-model
   ```

2. Reference them in profile partials via `custom_providers`.

### Cloud Provider Setup

The system uses Ollama as a proxy for both local and cloud models. Cloud models are accessed through Ollama's cloud model support:

1. Sign into Ollama in the container:
   ```bash
   docker exec -it bob-the-agent-ollama ollama signin
   ```

2. Pull cloud model manifests:
   ```bash
   docker exec bob-the-agent-ollama ollama pull kimi-k2.7-code:cloud
   ```

3. Reference in profile partials as shown above. Note the local ollama container cannot serve `:cloud` models until it is signed in.

## Observability (Logfire)

The harness ships [Logfire](https://logfire.pydantic.dev) instrumentation (added Nov 2026, commits `d230759` + `ec2dd44`; dependency `logfire[system-metrics]>=6` in `src/bob-harness/requirements.txt`, deliberately keeping the version cap style used by the rest of the requirements set).

**Enablement is config-driven, in two parts:**

- YAML: `logfire.enabled` — template default `true` in `src/bob-harness/bob-harness.template.yaml`, built-in default `false` when the key is absent (`config.py` `_BUILTIN_DEFAULTS`). The YAML also carries `logfire.environment` (`'local'`), parsed into `LogfireConfig`.
- Env: `LOGFIRE_TOKEN` — compose passes it through; it is the ingest token and the only user-facing knob. Without a token, the instrumentation still initializes but nothing is exported to Logfire.

**When `logfire.enabled: true`, the supervisor (`src/bob-harness/harness/supervisor.py`) runs, exactly once per process:**

1. `logfire.configure(environment='local', service_name='bob-harness')` — note the supervisor currently pins `environment='local'` and does not read the YAML `logfire.environment` field
2. `logfire.instrument_system_metrics()` — host/container system metrics (this is what the `[system-metrics]` extra is for)
3. `logfire.instrument_httpx()` — spans for outbound HTTP calls
4. `logfire.instrument_pydantic_ai()` — spans for the router-brain agent runs; the two agents are named (`name='route_agent'` / `name='analyze_agent'` in `src/bob-harness/harness/router.py`) so traces are distinguishable in the Logfire UI

**Failure isolation:** the whole block is wrapped in `try/except` — a failed Logfire setup (e.g. a broken token or an offline collector) is logged as an error and **never aborts the bob-harness boot**.

Instrumentation lives only in the supervisor: `RouterBrain` no longer configures Logfire itself (commit `ec2dd44` moved it out so there is a single `configure()` call per process).

## Security Considerations

### Password Security

- Use strong passwords (16+ characters)
- Change default credentials immediately

### Network Security

- The bob-harness gateway is published on loopback only (127.0.0.1:8181). Its API-key auth secures every route except the minimal unauthenticated `GET /healthz`; widen the publish only in front of a reverse proxy, and keep it off untrusted networks
- Use Docker network isolation (services communicate on the internal `bob-the-agent-network`)

### API Keys

- Never commit API keys to git
- Use environment variables (`.env` file)
- Rotate keys periodically

## Performance Tuning

### Memory Allocation

For limited RAM, reduce the agent container's limits:

```yaml
agent:
  deploy:
    resources:
      limits:
        memory: 4G
```

Profiles are multiplexed in the one container, so there is no per-agent container to disable — reducing limits affects all profiles.

### Model Selection

- Use cloud models (the current defaults: `glm-5.3-flash:cloud` for simple, `glm-5.3:cloud` for researcher) for best quality (requires `ollama signin` in the ollama container)
- Local models remain usable for offline operation — pull one with `ollama pull` and reference it in a profile partial; the current template defines no local default
- Profiles override models via each partial (see [Changing Profile Models](#changing-profile-models))

### Profile Count

Two profiles (`researcher`, `simple`) run inside the single container. To reduce load, remove a profile partial from `src/agents/` — profiles are provisioned only from that directory, so a profile without a partial there is not provisioned at the next boot.
