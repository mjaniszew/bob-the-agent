# Architecture Overview

This document describes the architecture of Bob The Agent system.

## Container Architecture

The system runs as a single container managed by Docker Compose. The orchestrator is the **Python harness** (pydantic-AI-based, gateway :8181) that delegates to two Hermes profiles (`researcher`, `simple`) and OpenCode CLI via one-shot subprocesses — there is no messaging transport and no per-agent container. It shares a common Ollama model provider and SearXNG search engine with the infra containers. The image is `bob-the-agent:latest`, built from `dockerfiles/Dockerfile.hermes` (based on the official Hermes Agent image `nousresearch/hermes-agent:v2026.9.14`, Hermes v0.21.3).

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
│  │  Python harness (the orchestrator; task 22 replaces the old │         │
│  │  gateway; replaces the old "main"-profile orchestration):  │         │
│  │  ├─ gateway (:8181, FastAPI + htmx UI, X-API-Key auth)      │         │
│  │  ├─ pydantic-AI router brain (hermes vs opencode + profile)  │        │
│  │  ├─ Discord bot (single identity, harness-owned)            │         │
│  │  └─ taskloop + scheduler (inbox queue, cron jobs)           │         │
│  │                                                             │         │
│  │  Hermes profiles (/opt/data/profiles/):                     │         │
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
│  │  - ./volumes/agent (single agent workspace: harness state  │       │
│  │    under /harness, profiles under profiles/)                │       │
│  │  - ./volumes/projects (shared project workspace, opencode   │       │
│  │    workdir root)                                             │       │
│  │  - ./volumes/results (task outputs)                          │       │
│  └──────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────┘
```

### In-process Delegation

Agents are invoked **in-process** as one-shot subprocesses — there is no messaging layer. The harness router (`RouterBrain`) picks the agent + hermes profile; the adapter runs the CLI directly:

- **Hermes**: `hermes -p <profile> chat --oneshot --query-file <tmpfile> ...` (task 22: ALWAYS a provisioned profile — `simple` for quick chat/simple tasks, `researcher` for research/analysis; the router LLM picks with keyword fallbacks and `agents.hermes.default_profile` as the safety net)
- **OpenCode**: `opencode run <prompt> --format json ...` inside a validated workdir under `/app/projects` (task-inbox tasks carry their own workdir; chat/Discord/scheduled runs get `/app/projects/harness-sessions/<session-id>`)
- **File handoff / results**: task result files land under the task dir (`tasks/<id>/result.md`); `/app/results` (mounted at `./volumes/results`) remains the payload drop for handoff-style flows

Per-run timeouts live in the harness config (`agents.*.timeout_s`, `agents.hermes.profile_timeouts`), enforced by the adapter watchdog (process-group kill). Runs are streamed into the session transcript and (for tasks) recorded in the runs ledger.

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | LLM inference engine (local and cloud models) | 11434 |
| `agent` | Single container: the Python harness (gateway/UI :8181, router, Discord bot, task loop, scheduler) delegating to the Hermes profiles (`researcher`, `simple`) and OpenCode | 8181 (harness gateway, loopback) |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent` depends on `ollama` (LLM inference, healthy) and `searxng` (started)
- `searxng` depends on `valkey` (caching)

The compose `agent` healthcheck is a TCP check on port 8181 (`start_period: 120s`). The harness gateway answers `{"ok":true}` on `/healthz` and serves the UI at `/` (task 22: bootstrap execs `python -m harness.supervisor` directly — no supervisord layer; the legacy per-profile Hermes gateway is held down by the `02-harness-only` cont-init hook so the harness bot owns the Discord identity).

### Agent Profiles

The orchestrator is the **Python harness** (`/app/harness`, gateway/UI + router + Discord bot + task loop + scheduler) — there is no `main` profile. The Hermes profiles are one-shot workers the harness invokes:

| Profile | Model (default) | Role |
|-------|-------|------|
| `researcher` | `glm-5.3:cloud` (cloud, via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple` | `glm-5.3-flash:cloud` (cloud, via Ollama) | Simple task handler — web search, summaries, data extraction, S3 uploads; the `agents.hermes.default_profile` chat fallback |

Model defaults live in `src/config/hermes.template.yaml`; per-profile overrides (e.g. the per-request `ollama_num_ctx` context pins) are set in `src/agents/{name}/hermes.partial.yml` (see [Configuration Guide](./CONFIGURATION.md)). GLM cloud limits: 512000-token context, 32000-token output.

Coding tasks are handled by **OpenCode CLI** (its own agent, not a Hermes profile — task 22 removed the coder profile): OpenCode CLI is installed at image build time (`npm install -g opencode-ai@1.18.32`, a deliberate pin — not `@latest`) and invoked by the harness as `opencode run <prompt> --format json` inside a validated workdir under `/app/projects`. Its agent models (`src/config/opencode.template.jsonc` → `opencode.jsonc`): `build` primary = `glm-5.3-flash:cloud`, `plan` primary = `glm-5.3:cloud`, `code-reviewer` primary = `kimi-k2.7-code:cloud` (read-only: write/edit tools disabled); its subagents (`general`, `explore`, `scout`) use `glm-5.3-flash:cloud`.

### OpenCode: Coding Engine

```
┌─────────────────────────────────────────────────────────────────┐
│                OpenCode CLI (in the agent container)            │
│                                                                 │
│  - Invoked per run by the harness opencode adapter:             │
│    `opencode run <prompt> --format json` (NDJSON event stream)  │
│  - workdir is mandatory and must be nested under /app/projects  │
│    (task workdir, or /app/projects/harness-sessions/<session>)  │
│  - Configured via opencode.jsonc (env-substituted template)     │
│                                                                 │
│  Volumes:                                                       │
│  - /app/projects     <- shared git repos and code              │
│  - /app/results      <- task output files                      │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration Flow

The container runs s6 as PID 1 and uses `/app/scripts/bootstrap.sh` as the container command; the script provisions the two Hermes profiles (`researcher`, `simple`), seeds the harness state, and then execs the harness long-runner directly (task 22: `python -m harness.supervisor` — no supervisord layer; the legacy hermes gateway never auto-runs, see the `02-harness-only` cont-init hook):

```
┌──────────────────────────────────────────────────────────────┐
│                    Container Startup Flow                      │
│                                                               │
│  bootstrap.sh (container CMD /app/scripts/bootstrap.sh)       │
│     │                                                         │
│     ├── 2. Hermes profiles (researcher, simple)               │
│     │   Each under /opt/data/profiles/{name}/:                │
│     │   ├── config.yaml missing? → generate-config.sh         │
│     │   │   (merge hermes.template.yaml + the profile partial │
│     │   │    via merge-yaml.mjs, partial wins)                │
│     │   ├── config.yaml exists? → reconcile with the current  │
│     │   │   partial (one-time .pre-reconcile.bak snapshot)    │
│     │   └── SOUL.md, skills, profile.yaml, per-profile .env   │
│     │   Each under /opt/data/profiles/{name}/:                │
│     │   ├── config.yaml (generate or reconcile, as above)     │
│     │   ├── SOUL.md, skills/, profile.yaml bot roster         │
│     │   │   (ui_meta.hermes-bots), per-profile .env           │
│     │   │   (umask 077 then chmod 600)                        │
│     │   └── Bot Chat session (message_agent path; best-effort)│
│     │                                                         │
│     ├── 3. OpenCode CLI config (written unconditionally)      │
│     │   └── env-substitute opencode.template.jsonc →          │
│     │       /opt/data/.config/opencode/opencode.jsonc         │
│     │                                                         │
│     ├── 4. Harness state root (+ skills seed)                 │
│     │   └── /opt/data/harness (config from template once,     │
│     │       memory seed, skills)                              │
│     ├── 5. Default Ollama model                               │
│     │   └── Pull the template default glm-5.3-flash:cloud if  │
│     │       missing (best-effort, capped at 30 minutes); a    │
│     │       cloud manifest pull, not a large local download   │
│     │                                                         │
│     └── 5. exec python -m harness.supervisor                  │
│          (the gateway/UI fronts the harness; container        │
│           lifetime equals the harness process)                │
└──────────────────────────────────────────────────────────────┘
```

Bootstrap is idempotent — a container restart re-runs provisioning safely before exec-ing the harness again.

### Key Files

| File | Purpose |
|------|---------|
| `src/config/hermes.template.yaml` | Shared Hermes config template (model defaults, toolsets, agent settings; sets `gateway.multiplex_profiles: true`, which applies globally to the single gateway) |
| `src/agents/{name}/hermes.partial.yml` | Per-profile config overrides (e.g. per-request Ollama context via `ollama_num_ctx`) |
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/config/opencode.template.jsonc` | OpenCode CLI config template (env-substituted at startup) |
| `src/scripts/bootstrap.sh` | Container command: provisions profiles + harness state, then execs `python -m harness.supervisor` |
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
|   |   /opt/data/ (agent workspace)                       |           |
|   |   - harness/ (sessions, tasks, memory, skills,      |           |
|   |     ledger, scheduler state)                         |           |
|   |   - memories/ (legacy seed)                          |           |
|   |   - config.yaml (Hermes default, unused by harness)  |           |
|   |   - profiles/{researcher,simple}/                    |           |
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

The harness decides agent + profile per turn/task via `RouterBrain.route_async`/`analyze_async` (pydantic-AI LLM call; keyword fallback when the provider is down; NEVER raises for provider problems — F4/F5):

1. The chat turn / Discord message / inbox task / scheduled job is routed to `hermes` or `opencode`
2. A `RunSpec` is built: hermes always carries `-p <profile>` (LLM-picked `simple`/`researcher`; config default `agents.hermes.default_profile` when the route omits it), opencode carries a validated workdir under `/app/projects`
3. The adapter streams the child's stdout into the session transcript (chunks, tool events, exactly one terminal `final`/`failed`)
4. Task results are written to `tasks/<id>/result.md`; the FSM moves the task to DONE/FAILED; `/app/results` remains the payload drop


### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/opt/data` | `./volumes/agent` | Main (default) profile workspace (config, SOUL.md, memory, skills) |
| `/opt/data/profiles/{researcher,simple}` | `./volumes/agent/profiles/...` | Secondary profile workspaces (config, SOUL.md, skills, per-profile `.env`) |
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

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs
