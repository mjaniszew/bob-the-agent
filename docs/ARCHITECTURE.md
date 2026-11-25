# Architecture Overview

This document describes the architecture of Bob The Agent system.

## Container Architecture

The system runs as a single container managed by Docker Compose. The orchestrator is the **Python bob-harness** (pydantic-AI-based, gateway :8181) that delegates to two Hermes profiles (`researcher`, `simple`) and OpenCode CLI via one-shot subprocesses — there is no messaging transport and no per-agent container. It shares a common Ollama model provider and SearXNG search engine with the infra containers. The image is `bob-the-agent:latest`, built from `dockerfiles/Dockerfile.hermes` (based on the official Hermes Agent image `nousresearch/hermes-agent:v2026.9.14`, Hermes v0.21.3).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Docker Compose                                │
│                                                                         │
│  ┌───────────┐                                                          │
│  │  ollama   │                                                          │
│  │  :11434   │                                                          │
│  │  LLM      │                                                          │
│  │ Inference │                                                          │
│  └─────┬─────┘                                                          │
│        │                                                                 │
│  ┌─────┴──────────────────────────────────────────────────────┐         │
│  │  agent (single container)                                   │         │
│  │                                                             │         │
│  │  Python bob-harness (the orchestrator; task 22 replaces the old │         │
│  │  gateway; replaces the old "main"-profile orchestration):  │         │
│  │  ├─ gateway (:8181, FastAPI + htmx UI, X-API-Key auth)      │         │
│  │  ├─ pydantic-AI router brain (hermes vs opencode + profile)  │        │
│  │  ├─ Discord bot (single identity, bob-harness-owned)            │         │
│  │  └─ taskloop + scheduler (inbox queue, cron jobs)           │         │
│  │                                                             │         │
│  │  Hermes profiles (/opt/data/hermes/profiles/):                     │         │
│  │  ┌────────────┐ ┌────────────┐                              │         │
│  │  │ researcher │ │ simple     │                              │         │
│  │  │ research   │ │ simple     │                              │         │
│  │  │ specialist │ │ tasks      │                              │         │
│  │  └────────────┘ └────────────┘                              │         │
│  │  called via `hermes -p <profile> chat --oneshot`            │         │
│  │  (one-shot subprocess per run; always a provisioned profile)│         │
│  │  OpenCode CLI = coding engine (`opencode run`, workdir      │         │
│  │  under /app/projects)                                        │        │
│  └─────────────────────────────────────────────────────────────┘         │
│                                                                         │
│  ┌──────────────────────────────────────────────────┐                   │
│  │          SearXNG + Valkey                          │                   │
│  │  ┌──────────┐       ┌──────────┐                  │                   │
│  │  │ searxng  │◄──────│  valkey  │                  │                   │
│  │  │  :8888   │       │  :6379   │                  │                   │
│  │  │Web Search│       │  Cache   │                  │                   │
│  │  └──────────┘       └──────────┘                  │                   │
│  └──────────────────────────────────────────────────┘                   │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │                      Docker Volumes                           │       │
│  │  - ollama_data (models)                                      │       │
│  │  - searxng_config, searxng_data (search engine)              │       │
│  │  - valkey_data (cache)                                       │       │
│  │  - ./volumes/agent (single agent workspace: bob-harness state │       │
│  │    under bob-harness/, profiles under hermes/profiles/)                │       │
│  │  - ./volumes/projects (shared project workspace, opencode   │       │
│  │    workdir root)                                             │       │
│  │  - ./volumes/results (task outputs)                          │       │
│  └──────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────┘
```

### In-process Delegation

Agents are invoked **in-process** as one-shot subprocesses — there is no messaging layer. The bob-harness router (`RouterBrain`) picks the agent + hermes profile; the adapter runs the CLI directly:

- **Hermes**: `hermes -p <profile> chat --oneshot --query-file <tmpfile> ...` (task 22: ALWAYS a provisioned profile — `simple` for quick chat/simple tasks, `researcher` for research/analysis; the router LLM picks with keyword fallbacks and `agents.hermes.default_profile` as the safety net)
- **OpenCode**: `opencode run <prompt> --format json ...` inside a validated workdir under `/app/projects` (task-inbox tasks carry their own workdir; chat/Discord/scheduled runs get `/app/projects/bob-harness-sessions/<session-id>`)
- **File handoff / results**: task result files land under the task dir (`tasks/<id>/result.md`); `/app/results` (mounted at `./volumes/results`) remains the payload drop for handoff-style flows

Per-run timeouts live in the bob-harness config (`agents.*.timeout_s`, `agents.hermes.profile_timeouts`), enforced by the adapter watchdog (process-group kill). Runs are streamed into the session transcript and (for tasks) recorded in the runs ledger.

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | LLM inference engine (local and cloud models) | 11434 |
| `agent` | Single container: the Python harness (gateway/UI :8181, router, Discord bot, task loop, scheduler) delegating to the Hermes profiles (`researcher`, `simple`) and OpenCode | 8181 (bob-harness gateway, loopback) |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent` depends on `ollama` (LLM inference, healthy) and `searxng` (started)
- `searxng` depends on `valkey` (caching)

The compose `agent` healthcheck is a TCP check on port 8181 (`start_period: 120s`). The harness gateway answers `{"ok":true}` on `/healthz` and serves the UI at `/` (task 22: bootstrap execs `python -m bob_harness.supervisor` directly — no supervisord layer; the legacy per-profile Hermes gateway is held down by the `02-bob-harness-only` cont-init hook so the harness bot owns the Discord identity).

### Agent Profiles

The orchestrator is the **Python harness** (`/app/bob-harness`, gateway/UI + router + Discord bot + task loop + scheduler) — there is no `main` profile. The Hermes profiles are one-shot workers the harness invokes:

| Profile | Model (default) | Role |
|-------|-------|------|
| `researcher` | `glm-5.3:cloud` (cloud, via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple` | `glm-5.3-flash:cloud` (cloud, via Ollama) | Simple task handler — web search, summaries, data extraction, S3 uploads; the `agents.hermes.default_profile` chat fallback |

Model defaults live in `src/config/hermes.template.yaml`; per-profile overrides (e.g. the per-request `ollama_num_ctx` context pins) are set in `src/agents/{name}/hermes.partial.yml` (see [Configuration Guide](./CONFIGURATION.md)). GLM cloud limits: 512000-token context, 32000-token output.

Coding tasks are handled by **OpenCode CLI** (its own agent, not a Hermes profile — task 22 removed the coder profile): OpenCode CLI is installed at image build time (`npm install -g opencode-ai@1.18.32`, a deliberate pin — not `@latest`) and invoked by the bob-harness as `opencode run <prompt> --format json` inside a validated workdir under `/app/projects`. Its agent models (`src/config/opencode.template.jsonc` → `opencode.jsonc`): `build` primary = `glm-5.3-flash:cloud`, `plan` primary = `glm-5.3:cloud`, `code-reviewer` primary = `kimi-k2.7-code:cloud` (read-only: write/edit tools disabled); its subagents (`general`, `explore`, `scout`) use `glm-5.3-flash:cloud`.

### OpenCode: Coding Engine

```
┌─────────────────────────────────────────────────────────────────┐
│                OpenCode CLI (in the agent container)            │
│                                                                 │
│  - Invoked per run by the bob-harness opencode adapter:             │
│    `opencode run <prompt> --format json` (NDJSON event stream)  │
│  - workdir is mandatory and must be nested under /app/projects  │
│    (task workdir, or /app/projects/bob-harness-sessions/<session>)  │
│  - Configured via opencode.jsonc (env-substituted template)     │
│                                                                 │
│  Volumes:                                                       │
│  - /app/projects     <- shared git repos and code              │
│  - /app/results      <- task output files                      │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration Flow

The container runs s6 as PID 1 and uses `/app/scripts/bootstrap.sh` as the container command; the script provisions the two Hermes profiles (`researcher`, `simple`), seeds the harness state, and then execs the harness long-runner directly (task 22: `python -m bob_harness.supervisor` — no supervisord layer; the legacy hermes gateway never auto-runs, see the `02-bob-harness-only` cont-init hook):

```
┌──────────────────────────────────────────────────────────────┐
│                    Container Startup Flow                      │
│                                                               │
│  bootstrap.sh (container CMD /app/scripts/bootstrap.sh)       │
│     │                                                         │
│     ├── 2. Hermes profiles (researcher, simple)               │
│     │   Each under /opt/data/hermes/profiles/{name}/:                │
│     │   ├── config.yaml missing? → generate-config.sh         │
│     │   │   (merge hermes.template.yaml + the profile partial │
│     │   │    via merge-yaml.mjs, partial wins)                │
│     │   ├── config.yaml exists? → reconcile with the current  │
│     │   │   partial (one-time .pre-reconcile.bak snapshot)    │
│     │   └── SOUL.md, skills, profile.yaml, per-profile .env   │
│     │   Each under /opt/data/hermes/profiles/{name}/:                │
│     │   ├── config.yaml (generate or reconcile, as above)     │
│     │   ├── SOUL.md, skills/, profile.yaml bot roster         │
│     │   │   (ui_meta.hermes-bots), per-profile .env           │
│     │   │   (umask 077 then chmod 600)                        │
│     │   └── Bot Chat session (message_agent path; best-effort)│
│     │                                                         │
│     ├── 3. OpenCode CLI config (written unconditionally)      │
│     │   └── env-substitute opencode.template.jsonc →          │
│     │       /opt/data/opencode/opencode.jsonc         │
│     │                                                         │
│     ├── 4. bob-harness state root (+ skills seed)                 │
│     │   └── /opt/data/bob-harness (config from template once,     │
│     │       memory seed, skills)                              │
│     ├── 5. Default Ollama model                               │
│     │   └── Pull the template default glm-5.3-flash:cloud if  │
│     │       missing (best-effort, capped at 30 minutes); a    │
│     │       cloud manifest pull, not a large local download   │
│     │                                                         │
│     └── 5. exec python -m bob_harness.supervisor                  │
│          (the gateway/UI fronts the bob-harness; container        │
│           lifetime equals the bob-harness process)                │
└──────────────────────────────────────────────────────────────┘
```

Bootstrap is idempotent — a container restart re-runs provisioning safely before exec-ing bob-harness again.

The harness config seeded in step 4 is pointed at via the compose env `BOB_HARNESS_CONFIG: /opt/data/bob-harness/config.yaml`. Without it `load_config()` looks for `./config.yaml` in the CWD (`/app`), finds none, and **silently falls back to built-in defaults** — every YAML-only setting (`logfire.enabled`, `rate_limit`, scheduler jobs) is discarded while flat `BOB_HARNESS_*` env overrides still apply. Regression fixed in commit `ec2dd44`; `tests/harness-contracts.test.ts` asserts the seven `BOB_HARNESS_*` keys compose exposes, `BOB_HARNESS_CONFIG` included. See [Configuration Guide](./CONFIGURATION.md) for the file/precedence description and the [Observability (Logfire)](./CONFIGURATION.md#observability-logfire) section.

### Key Files

| File | Purpose |
|------|---------|
| `src/config/hermes.template.yaml` | Shared Hermes config template (model defaults, toolsets, agent settings; sets `gateway.multiplex_profiles: true`, which applies globally to the single gateway) |
| `src/agents/{name}/hermes.partial.yml` | Per-profile config overrides (e.g. per-request Ollama context via `ollama_num_ctx`) |
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/config/opencode.template.jsonc` | OpenCode CLI config template (env-substituted at startup) |
| `src/bob-harness/bob-harness.template.yaml` | Harness config template (server bind, `logfire`, `rate_limit`, `router`, `agents.hermes.default_profile`); seeded once to `/opt/data/bob-harness/config.yaml` by bootstrap, pointed at via `BOB_HARNESS_CONFIG` |
| `src/scripts/bootstrap.sh` | Container command: provisions profiles + harness state, then execs `python -m bob_harness.supervisor` |
| `src/scripts/generate-config.sh` | Config generation orchestrator |
| `src/scripts/merge-yaml.mjs` | YAML deep-merge utility (partial wins on overlapping keys) |
| `src/scripts/migrate-volumes.sh` | One-time migration of old per-agent volumes into `./volumes/agent` |
| `src/config/searxng.settings.yml` | SearXNG configuration |

## Skills Architecture

Skills live in standalone directories under `/app/skills/<name>/`, each carrying its own `SKILL.md` (interface documentation), its implementation, and its own dependency manifest (`package.json`; TypeScript skills are invoked via a per-skill `cli.ts`). Invocation is direct, following each `SKILL.md`: `node /app/skills/<name>/cli.ts '<json>'` for TypeScript skills and `python3` for the searxng-web-search helper script. There is no central runner and no tsc build — `/app/skills` has no `dist/`; per-skill runtime dependencies are installed at image build time.

```
src/skills/
├── index.ts                    # Skill registry (tests/jest use; runtime invokes skills directly)
├── delegate-profile/           # In-process profile delegation
│   ├── SKILL.md
│   ├── cli.ts                  # node /app/skills/delegate-profile/cli.ts '<json>'
│   ├── package.json
│   └── index.ts
├── searxng-web-search/         # Python: stdlib-only, no dependency manifest
│   ├── SKILL.md
│   └── scripts/
│       └── searxng_search.py   # python3 /app/skills/searxng-web-search/scripts/searxng_search.py
├── grok-search/
│   ├── SKILL.md
│   ├── cli.ts
│   ├── package.json
│   └── index.ts
├── x-com/
│   ├── SKILL.md
│   ├── cli.ts
│   ├── package.json
│   └── index.ts
├── aws-s3/
│   ├── SKILL.md
│   ├── cli.ts
│   ├── package.json
│   ├── package-lock.json       # committed; Dockerfile `npm ci` uses it
│   └── index.ts
├── data-extraction/
│   ├── SKILL.md
│   ├── cli.ts
│   ├── package.json
│   └── index.ts
└── math-operations/
    ├── SKILL.md
    ├── cli.ts
    ├── package.json
    └── index.ts
```

Skills ship with their manifests: `src/skills/` is copied as-is to `/app/skills` during the Docker build and per-skill runtime dependencies are installed right there (aws-s3 is currently the only skill with runtime dependencies — installed via a per-skill `npm ci` with a BuildKit npm cache mount; the stdlib-only skills need nothing). There is no compilation step — the image's Node runs the skills' `.ts` files natively via type stripping. At container startup `bootstrap.sh` resets and copies each skill directory into `/opt/data/skills/` and every profile's `skills/` directory (skipping `node_modules`) so the Hermes agent's skills system lists them; runtime invocation follows each SKILL.md.

### Skill Integration Status

| Skill | Status | Description |
|-------|--------|-------------|
| searxng-web-search | ✅ Active | Web search via local SearXNG instance (free, no API keys) |
| grok-search | ✅ Active | X.com search via xAI Grok's x_search tool (fallback for x-com) |
| x-com | ✅ Active | Direct X.com (Twitter) API access for posts, users, timelines |
| aws-s3 | ✅ Active | Upload files and generate presigned URLs for S3 |
| data-extraction | ✅ Implemented | Extract structured data from websites/documents |
| math-operations | ✅ Implemented | Mathematical calculations and operations |
| delegate-profile | ✅ Active | In-process task delegation to the researcher/simple profiles |

Skills are invoked directly (see each SKILL.md for flags and parameters):
```bash
node /app/skills/<skill-name>/cli.ts '<json-params>'
```
(`python3` for the searxng-web-search helper script — `python3 /app/skills/searxng-web-search/scripts/searxng_search.py`.)

## Data Flow

```
+----------------------------------------------------------------------+
|                           Data Flow                                  |
|                                                                      |
|  User -> Discord bot / gateway UI (:8181)                            |
|                        |                                             |
|                        v                                             |
|   Router brain (pydantic-AI LLM): agent + profile | opencode         |
|                        |                                              |
|           +------------+--------------+                              |
|           v                           v                              |
|      hermes adapter            opencode adapter                      |
|      hermes -p <profile>       opencode run --format json            |
|      chat --oneshot            (validated workdir under              |
|                                 /app/projects)                       |
|           |                           |                              |
|           v                           v                              |
|   +-----------------------------------------------------+           |
|   |   /opt/data/ (agent workspace; task 23 folder split)  |           |
|   |   - bob-harness/  (bob-harness state: config.yaml,    |           |
|   |     sessions, tasks, memory, skills, ledger,          |           |
|   |     scheduler state, logs)                            |           |
|   |   - hermes/       (hermes agent state: HERMES_HOME,   |           |
|   |     profiles/{researcher,simple}/, memories)          |           |
|   |   - opencode/     (opencode config + XDG state)       |           |
|   +-----------------------------------------------------+           |
|                        |                                             |
|                        v                                             |
|                 tasks/<id>/result.md +                                |
|                 ./volumes/results/                                    |
|              (final output to user)                                  |
|                                                                      |
|   +----------------------------------------------------------+      |
|   |                    External Services                      |     |
|   |   SearXNG (:8888) | X.com API | xAI Grok | AWS S3         |     |
|   +----------------------------------------------------------+      |
+----------------------------------------------------------------------+
```

### Agent Run Protocol

The bob-harness decides agent + profile per turn/task via `RouterBrain.route_async`/`analyze_async` (pydantic-AI LLM call; keyword fallback when the provider is down; NEVER raises for provider problems — F4/F5):

1. The chat turn / Discord message / inbox task / scheduled job is routed to `hermes` or `opencode`
2. A `RunSpec` is built: hermes always carries `-p <profile>` (LLM-picked `simple`/`researcher`; config default `agents.hermes.default_profile` when the route omits it), opencode carries a validated workdir under `/app/projects`
3. The adapter streams the child's stdout into the session transcript (chunks, tool events, exactly one terminal `final`/`failed`)
4. Task results are written to `tasks/<id>/result.md`; the FSM moves the task to DONE/FAILED; `/app/results` remains the payload drop


### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/opt/data/bob-harness` | `./volumes/agent/bob-harness/...` | bob-harness state root: config.yaml, sessions, tasks, memory, skills, logs, runs ledger, scheduler state |
| `/opt/data/hermes` (`HERMES_HOME`) | `./volumes/agent/hermes/...` | Hermes agent state: profiles/, memories/ |
| `/opt/data/hermes/profiles/{researcher,simple}` | `./volumes/agent/hermes/profiles/...` | Profile workspaces (config, SOUL.md, skills, per-profile `.env`) |
| `/opt/data/opencode` | `./volumes/agent/opencode/...` | OpenCode config (opencode.jsonc) + XDG state (`config/`, `data/`, `state/`, `cache/`) |
| `/app/results` | `./volumes/results` | Agents results data |
| `/app/projects` | `./volumes/projects` | Shared project workspace (git repos, code) |
| `/root/.ollama` | `ollama_data` volume | Downloaded models |
| `/etc/searxng` | `searxng_config` volume | SearXNG configuration |
| `/var/cache/searxng` | `searxng_data` volume | SearXNG cache |
| `/data` | `valkey_data` volume | Valkey/Redis data |

## Environment Variables

See `.env.template` for all configurable variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OLLAMA_BASE_URL` | No | Ollama API endpoint (defaults to http://ollama:11434) |
| `DISCORD_BOT_TOKEN` | For Discord | Bot token from Discord Developer Portal |
| `SEARXNG_BASE_URL` | No | SearXNG URL (defaults to http://searxng:8888) |
| `USER_X_COM_API_TOKEN` | For X.com | X.com API token for x-com skill |
| `USER_XAI_SEARCH_API_KEY` | For Grok | xAI API key for grok-search skill |
| `USER_AWS_S3_ACCESS_KEY_ID` | For AWS S3 | AWS access key |
| `USER_AWS_S3_SECRET_ACCESS_KEY` | For AWS S3 | AWS secret key |
| `USER_AWS_S3_BUCKET` | For AWS S3 | S3 bucket name |
| `USER_AWS_S3_REGION` | For AWS S3 | S3 bucket region |
| `OLLAMA_CONTEXT_LENGTH` | No | Ollama server context length (compose default: 65536). Known tension: `hermes.partial.yml` pins `ollama_num_ctx` to 256000 for researcher (65536 for simple), which can exceed this default |
| `HERMES_API_TIMEOUT` | No | Hermes API read timeout in seconds (default: 3600) |
| `HERMES_UID` / `HERMES_GID` | No | Linux hosts: run the container with these UID/GID so `./volumes/*` bind mounts are owned by your user |
| `LOG_LEVEL` | No | Logging level (info, debug, warn, error) |
| `LOGFIRE_TOKEN` | No | Logfire ingest token — remote export token for the harness's built-in Logfire instrumentation (pydantic-AI router/analyze agent spans, httpx, system metrics; service name `bob-harness`). Gated by `logfire.enabled` in the harness YAML (template default `true`); unset = no remote export |

<!-- append-anchor: P9 pydantic-ai v2 architecture -->

## Pydantic AI v2 Architecture

The branch `custom-harness-poc` migrates the bob-harness from the pydantic-ai 1.x
API to **pydantic-ai 2.73.0** with the harness capability stack
(**pydantic-ai-harness[coder]==0.73.0**), implemented across phases P0-P8 of
`docs/plans/10-pydantic-ai-v2-migration-plan.md` (task 10). This section renders
the decisions and the capability manifest from that plan (§2, §3.2); the plan doc
is binding and carries the full rationale.

### Dependency pins (requirements.lock, frozen at P0)

| Package | Pin | Role |
|---|---|---|
| `pydantic-ai` | `pydantic-ai==2.73.0` | Router brain + executor agent framework (v2 `Agent` API) |
| `pydantic-ai-slim` | `pydantic-ai-slim==2.73.0` | Core slim distribution (transitive, frozen) |
| `pydantic-graph` | `pydantic-graph==2.73.0` | v2 graph runtime (transitive, frozen) |
| `pydantic-evals` | `pydantic-evals==2.73.0` | v2 evals distribution (transitive, frozen) |
| `pydantic-ai-harness` | `pydantic-ai-harness[coder]==0.73.0` | Capability stack (Memory/Skills/AskUser) + FileMemoryStore + harness Skills loader |
| `json-repair` | `json-repair==0.64.9` | Hard import requirement of the harness package (`[coder]` extra keeps it installed) |
| `pydantic` | `pydantic==2.14.4` | Settings + structured outputs for both agents |
| `pydantic-core` | `pydantic-core==2.50.5` | pydantic runtime (transitive, frozen) |

Banner hygiene: every v2 import path sets `PYDANTIC_AI_NO_BANNER=1` (Dockerfile
ENV + compose env + `tests/conftest.py`) so supervisor logs and smoke output
stay identical in shape.

### Design decisions D1-D7

One-line rationale per decision (full text incl. rejected alternatives: plan §2):

- **D1 — Delegation via a custom capability (toolset), NOT SubAgents entries**
  — hermes/opencode stay real subprocess adapters speaking their native CLI
  protocols (argv snapshots, `--resume`/`--session`, child-env allowlist); a
  custom `delegate_run` capability exposes them as tools without faking the
  in-process `AbstractAgent` message graph that harness `SubAgents` entries
  would require.
- **D2 — `executor` adapter in the existing registry; taskloop remains sole
  invoker** — the v2 pydantic agent is wrapped as an `AgentRun` adapter
  registered alongside `hermes`/`opencode`; taskloop stays the single task
  runner and every consumer keeps the closed `StreamEvent` contract
  (`started/chunk/final/tool/failed/system`). Delegation inside executor runs
  is nested, never task-origin; SUBTASK_DROP remains a taskloop/FSM-side event.
- **D3 — WAITING_INPUT gate on deferred tool calls; persisted resume seed**
  — the executor's ask tool raises `CallDeferred` (AskUser capability), the run
  returns `DeferredToolRequests`, and TaskLoop parks the task exactly as today
  (FSM `RUNNING -> WAITING_INPUT`, `gates.jsonl`, 24h timer) while additionally
  persisting `run.all_messages_json()` + a `DeferredToolResults` seed into the
  task folder; `answer()` resumes the SAME run — parked questions survive
  restarts. Subprocess adapters keep the prompt-append resume untouched.
- **D4 — One shared memory store; plain-file format + budgets preserved** —
  `MemoryEngine` is reimplemented over
  `FileMemoryStore(<storage.data_dir>/memory, workspace=LocalWorkspaceBackend)`,
  the same store the executor's `Memory` capability binds: plain
  `MEMORY.md`/`USER.md`/`lessons.md` files with the existing char budgets, plus
  the read-observe-then-write SHA-256 content versions and audit ledger the SDK
  store provides.
- **D5 — Harness `Skills` capability on existing folders; SkillsEngine keeps
  INDEX.md** — the `Skills` capability mounts the existing
  `<data_dir>/skills/*/SKILL.md` folders (`missing_directories='skip'` on a
  fresh tree) while `SkillsEngine` stays the sole owner of the
  byte-deterministic `INDEX.md` and the SKILL_DRAFT capture flow.
- **D6 — Harness coder capabilities NOT adopted** — `Coder`, `FileSystem`,
  `Shell`, `DockerContainer` (and `ModelRouter`) are not mounted on any agent;
  OpenCode remains the coder via delegation (D1). The only workspace machinery
  adopted is `LocalWorkspaceBackend` (memory/skills store bindings). The
  `[coder]` extra stays installed because the package hard-requires
  `json-repair` at import time.
- **D7 — Router stays capability-free** — `RouterBrain` keeps pure
  temperature-0 schema calls with one retry and the keyword fallback as the
  decision floor; capabilities would inject instructions/toolsets that break
  the `_PROMPT_CHAR_CAP` budget and add a failure mode to the never-blocking
  contract. The router consumes pre-capped projections (`digest()` ≤ 600
  chars, index ≤ 1500 chars) and never touches the memory/skills stores
  directly.

### Capability manifest — executor agent

| Capability | Mount / data binding | Config keys | Notes |
|---|---|---|---|
| `Memory` | `Memory(store=FileMemoryStore(<storage.data_dir>/memory, workspace=LocalWorkspaceBackend), agent_name='executor', inject_memory=True, max_tokens=2000)` — dir SHARED with `MemoryEngine` (D4) | `harness.memory.*` | Auto-injected memory per run; delegates get read-only injection as today |
| `Skills` | `Skills(directories=[<data_dir>/skills], missing_directories='skip', read_files=False)` over the existing SKILL.md folders; `INDEX.md` stays `SkillsEngine`-owned (D5) | `harness.skills.*` | Optional `include`/`exclude` slug filters; toolset exposes skill lookup to the executor |
| `AskUser` | `AskUser(answerer=None (deferred by default), id='ask_user')` — tool `ask_user_question`; events `AskUserRequestedEvent`/`AskUserAnsweredEvent` (`ctx.emit` → taskloop bus sink) | `harness.ask_user.*` | In task context the run returns `DeferredToolRequests` (D3); restart survival via the persisted message seed |
| `SubAgents` (nested only) | nested in-process helper agent entries (`max_depth=3`); NEVER wraps hermes/opencode (D1) | `harness.subagents.*` | Decompose/verify-style nested runs |
| `delegate_run` (custom) | custom `AbstractCapability` exposing `delegate_run(agent='hermes'\|'opencode', prompt, ...)` which calls the registered adapter's `run(spec)` and re-streams `StreamEvent`s into the executor run | `harness.delegation.*` | The mechanism by which Hermes and OpenCode act as delegated subagents (task DoD #2) without `SubAgents` entries; child-env rules untouched |

Explicitly NOT mounted (D6): `Coder`, `FileSystem`, `Shell`, `DockerContainer`,
`ModelRouter`. The executor's structured output streams to the session/task
transcript via the existing `StreamEvent(final|chunk)` kinds.

### Capability events on the bus (phase P8)

AskUser capability events are mirrored onto the existing `EventBus` topics
(additive; the closed `events.jsonl` kind registry is untouched):

- `harness.ask_user.requested` — `AskUserRequestedEvent` (CapabilityEvent base
  fields `kind`/`capability_id`/`tool_call_id`/`tool_name`)
- `harness.ask_user.answered` — `AskUserAnsweredEvent` (with
  `tool_call_id` + task id)

Scheduler task spawns keep flowing through taskloop unchanged (single-runner
pickup unchanged).

### Executor default runner (phase P7, the only behavior flip)

`tasks.runner` defaults to **`executor`** (the in-process v2 agent + harness
capabilities) since P7; `analyzed` restores the pre-migration behavior exactly
(router-analyzed agent + profile -> hermes/opencode CLI adapter). The flip is
merge-safe (single config default literal, no state migration); rollback lever
per environment: `BOB_HARNESS_TASKS_RUNNER=analyzed`.

<!-- append-anchor: P9 pydantic-ai v2 architecture end -->

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs
