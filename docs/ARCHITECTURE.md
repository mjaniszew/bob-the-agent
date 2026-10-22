# Architecture Overview

This document describes the architecture of Bob The Agent system.

## Container Architecture

The system runs as a single Hermes Agent container managed by Docker Compose. All specialized agents (main, researcher, simple, coder) are **Hermes profiles** inside that one container — there is no messaging transport and no per-agent container. The `agent` service runs the default profile (`main`) as the primary agent, with the gateway multiplexing the secondary profiles; it shares a common Ollama model provider and SearXNG search engine with the infra containers. The image is `bob-the-agent:latest`, built from `dockerfiles/Dockerfile.hermes` (based on the official Hermes Agent image `nousresearch/hermes-agent:v2026.9.14`, Hermes v0.21.3).

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
│  │  default profile "main"                                     │         │
│  │  ├─ Hermes agent (main orchestrator)                        │         │
│  │  ├─ multiplexed gateway (:8642)                             │         │
│  │  └─ Discord bot (outbound)                                  │         │
│  │                                                             │         │
│  │  secondary profiles (/opt/data/profiles/):                  │         │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────┐      │         │
│  │  │ researcher │ │ simple     │ │ coder              │      │         │
│  │  │ research   │ │ simple     │ │ Hermes + OpenCode  │      │         │
│  │  │ specialist │ │ tasks      │ │ CLI (coding engine)│      │         │
│  │  └────────────┘ └────────────┘ └────────────────────┘      │         │
│  │  delegated via `hermes -p <profile> chat --oneshot`         │         │
│  │  (in-process, delegate-profile skill)                       │         │
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
│  │  - ./volumes/agent (single agent workspace: main profile at  │       │
│  │    the root, researcher/simple/coder under profiles/)        │       │
│  │  - ./volumes/projects (shared project workspace for coder)   │       │
│  │  - ./volumes/results (task outputs)                          │       │
│  └──────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────┘
```

### In-process Delegation

Profiles are delegated to **in-process** — there is no messaging layer. The `delegate-profile` skill (used by the main profile) runs the target profile via the `hermes` CLI, exactly as an operator would:

- **Foreground**: `hermes -p <profile> chat --oneshot` runs the task to completion; the result summary is returned directly
- **Background**: the skill starts the same one-shot run detached with a log file and a `.running` marker, which `check_task` polls (state lives under `/opt/data/delegation/`)
- **File handoff**: result files are written to `/app/results` (mounted at `./volumes/results` on the host), passed via the `save_results_to` parameter

Each target has a fixed timeout enforced by the skill (simple 15m, researcher 60m, coder 120m). Orphaned background tasks (a `.running` marker that outlived the target's timeout + grace, e.g. after a container restart) are reported with `stale: true`; delegation logs and `.running` markers are auto-cleaned after 7 days. A gateway limitation prevents a multiplexed gateway session from messaging other profiles via `message_agent` (tracked upstream in Hermes issue #91260); the CLI-based delegation above is the working path today, and the per-profile Bot Chat sessions (`message_agent`/Bot Chat) created at bootstrap are the future messaging path once that issue is resolved.

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | LLM inference engine (local and cloud models) | 11434 |
| `agent` | Single Hermes Agent container — default profile `main` (orchestrator + Discord) with a gateway multiplexing the researcher/simple/coder profiles | 8642 (gateway); 9119 (dashboard) |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent` depends on `ollama` (LLM inference, healthy) and `searxng` (started)
- `searxng` depends on `valkey` (caching)

The compose `agent` healthcheck is a TCP check on port 8642 (`start_period: 120s`). The gateway answers 200 on `/health` and `/v1/health`; `/healthz` is not a gateway endpoint (404s). Port 9119 serves the Hermes dashboard (an s6 service), gated on the `HERMES_DASHBOARD` env var; non-loopback binds require authentication (`HERMES_DASHBOARD_BASIC_AUTH_USERNAME`/`_PASSWORD`/`_SECRET` or OAuth).

### Agent Profiles

| Profile | Model (default) | Role |
|-------|-------|------|
| `main` | `glm-5.3-flash:cloud` (cloud, via Ollama) | Main orchestrator — receives tasks, delegates to specialist profiles, provides Discord bot, manages results |
| `researcher` | `glm-5.3:cloud` (cloud, via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple` | `glm-5.3-flash:cloud` (cloud, via Ollama) | Simple task handler — web search, summaries, data extraction, S3 uploads |
| `coder` | `glm-5.3-flash:cloud` (cloud, via Ollama) | Coding specialist — Hermes profile orchestrating OpenCode CLI for software engineering tasks |

Model defaults live in `src/config/hermes.template.yaml`; per-profile overrides (e.g. the per-request `ollama_num_ctx` context pins) are set in `src/agents/{name}/hermes.partial.yml` (see [Configuration Guide](./CONFIGURATION.md)). GLM cloud limits: 512000-token context, 32000-token output.

The coder profile's OpenCode layer (`src/config/opencode.template.jsonc` → `opencode.jsonc`) configures its own agent models: `build` primary = `glm-5.3-flash:cloud`, `plan` primary = `glm-5.3:cloud`, `code-reviewer` primary = `kimi-k2.7-code:cloud` (read-only: write/edit tools disabled); its subagents (`general`, `explore`, `scout`) use `glm-5.3-flash:cloud`.

### Coder Profile: Two-Layer Architecture

Unlike the other profiles that use a single Hermes Agent instance, the coder profile uses a **two-layer architecture** where Hermes acts as a supervisor and communication bridge, and OpenCode CLI serves as the coding engine. Both layers live in the same container; OpenCode CLI is installed at image build time (`npm install -g opencode-ai@1.18.32`, a deliberate pin — not `@latest`):

```
┌─────────────────────────────────────────────────────────────────┐
│                Coder profile (in the agent container)            │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Layer 1: Hermes profile "coder" (Supervisor/Bridge)        ││
│  │                                                             ││
│  │  - Receives tasks via in-process delegation                 ││
│  │    (delegate-profile skill → hermes -p coder --oneshot)     ││
│  │  - Translates tasks into OpenCode prompts                   ││
│  │  - Monitors OpenCode via terminal/process tools             ││
│  │  - Reports results back (summary + /app/results files)      ││
│  │  - Sends status updates for long-running tasks             ││
│  │                                                             ││
│  │  Communication:  terminal() + process() tools               ││
│  │                  ──────────────────────                     ││
│  │                  ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼                     ││
│  ├─────────────────────────────────────────────────────────────┤│
│  │  Layer 2: OpenCode CLI (Coding Engine)                      ││
│  │                                                             ││
│  │  - Executes coding tasks (write, refactor, debug, review)   ││
│  │  - Operates on repos in /app/projects/                     ││
│  │  - Uses cloud GLM models from opencode.jsonc                ││
│  │  - Supports one-shot (opencode run) and                    ││
│  │    interactive modes                                        ││
│  │  - Configured via opencode.jsonc (env-substituted template) ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  Volumes:                                                       │
│  - /opt/data/profiles/coder <- profile workspace (config, etc.) │
│  - /app/projects     <- shared git repos and code              │
│  - /app/results      <- task output files                      │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration Flow

The container runs s6 as PID 1 and uses `/app/scripts/bootstrap.sh` as the container command; the script provisions the default (main) profile and the three secondary profiles, then execs the long-running gateway:

```
┌──────────────────────────────────────────────────────────────┐
│                    Container Startup Flow                      │
│                                                               │
│  bootstrap.sh (container CMD /app/scripts/bootstrap.sh)       │
│     │                                                         │
│     ├── 1. Default profile (main)                             │
│     │   ├── config.yaml missing? → generate-config.sh         │
│     │   │   (merge hermes.template.yaml + main partial via    │
│     │   │    merge-yaml.mjs, partial wins)                    │
│     │   └── config.yaml exists? → reconcile with the current  │
│     │       partial (one-time .pre-reconcile.bak snapshot;    │
│     │       partial wins, user-only keys preserved)           │
│     │   └── Copy SOUL.md + skills (skips node_modules/dist)   │
│     │                                                         │
│     ├── 2. Secondary profiles (researcher, simple, coder)     │
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
│     ├── 4. Default Ollama model                               │
│     │   └── Pull the template default glm-5.3-flash:cloud if  │
│     │       missing (best-effort, capped at 30 minutes); a    │
│     │       cloud manifest pull, not a large local download   │
│     │                                                         │
│     └── 5. exec hermes gateway run                            │
│          (gateway fronts all profiles; container lifetime     │
│           equals the gateway process)                         │
└──────────────────────────────────────────────────────────────┘
```

Bootstrap is idempotent — a container restart re-runs provisioning safely before exec-ing the gateway again.

### Key Files

| File | Purpose |
|------|---------|
| `src/config/hermes.template.yaml` | Shared Hermes config template (model defaults, toolsets, agent settings; sets `gateway.multiplex_profiles: true`, which applies globally to the single gateway) |
| `src/agents/{name}/hermes.partial.yml` | Per-profile config overrides (e.g. per-request Ollama context via `ollama_num_ctx`) |
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/config/opencode.template.jsonc` | OpenCode CLI config template for coder profile (env-substituted at startup) |
| `src/scripts/bootstrap.sh` | Container command: provisions all profiles, then execs the gateway |
| `src/scripts/generate-config.sh` | Config generation orchestrator |
| `src/scripts/merge-yaml.mjs` | YAML deep-merge utility (partial wins on overlapping keys) |
| `src/scripts/migrate-volumes.sh` | One-time migration of old per-agent volumes into `./volumes/agent` |
| `src/config/searxng.settings.yml` | SearXNG configuration |

## Skills Architecture

Skills live in standalone directories under `/app/skills/<name>/`, each carrying its own `SKILL.md` (interface documentation), its implementation, and its own dependency manifest (`package.json`; TypeScript skills are invoked via a per-skill `cli.ts`). Invocation is direct, following each `SKILL.md`: `node /app/skills/<name>/cli.ts '<json>'` for TypeScript skills and `python3` for the searxng-web-search helper script. There is no central runner and no tsc build — `/app/skills` has no `dist/`; per-skill runtime dependencies are installed at image build time.

```
src/skills/
├── index.ts                    # Skill registry
├── delegate-profile/
│   ├── SKILL.md                # Skill documentation
│   └── index.ts                # In-process profile delegation
├── searxng-web-search/
│   ├── SKILL.md                # Skill documentation
│   └── scripts/                # Python helper scripts
├── grok-search/
│   ├── SKILL.md
│   └── index.ts
├── x-com/
│   ├── SKILL.md
│   └── index.ts
├── aws-s3/
│   ├── SKILL.md
│   └── index.ts
├── data-extraction/
│   ├── SKILL.md
│   └── index.ts
└── math-operations/
    ├── SKILL.md
    └── index.ts
```

Skills are compiled during Docker build (`npm run build`) and copied to `/opt/data/skills/` (and each profile's `skills/`) at container startup. The Hermes agent accesses them via its skills system.

### Skill Integration Status

| Skill | Status | Description |
|-------|--------|-------------|
| searxng-web-search | ✅ Active | Web search via local SearXNG instance (free, no API keys) |
| grok-search | ✅ Active | X.com search via xAI Grok's x_search tool (fallback for x-com) |
| x-com | ✅ Active | Direct X.com (Twitter) API access for posts, users, timelines |
| aws-s3 | ✅ Active | Upload files and generate presigned URLs for S3 |
| data-extraction | ✅ Implemented | Extract structured data from websites/documents |
| math-operations | ✅ Implemented | Mathematical calculations and operations |
| delegate-profile | ✅ Active | In-process task delegation to the researcher/simple/coder profiles |

Skills are invoked directly (see each SKILL.md for flags and parameters):
```bash
node /app/skills/<skill-name>/cli.ts '<json-params>'
```
(`python3` for the searxng-web-search helper script — `python3 /app/skills/searxng-web-search/scripts/searxng_search.py`.)

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Data Flow                                  │
│                                                                      │
│  User → Discord → agent Gateway (:8642)                              │
│                        │                                             │
│                        ▼                                             │
│                  Main profile (Orchestrator)                          │
│                        │                                             │
│           ┌────────────┼──────────────┬──────────────┐               │
│           │            │              │              │                │
│           ▼            ▼              ▼              ▼                │
│     direct        delegate-profile  delegate-profile delegate        │
│     execution     (in-process)      (in-process)     (in-process)    │
│           │            │              │                              │
│           ▼            ▼              ▼                              │
│        main       researcher    simple          coder                │
│     (own work)    (deep research) (simple tasks) (Hermes + OpenCode)  │
│           │            │              │                              │
│           ▼            ▼              ▼                              │
│    ┌─────────────────────────────────────────────────────┐          │
│    │   /opt/data/ (main profile workspace)                │          │
│    │   - SOUL.md                                         │          │
│    │   - memories/ (daily notes)                         │          │
│    │   - config.yaml (Hermes config)                     │          │
│    │   - skills/, delegation/ (task logs & markers)      │          │
│    │   - profiles/{researcher,simple,coder}/             │          │
│    └─────────────────────────────────────────────────────┘          │
│                        │                                             │
│                        ▼                                             │
│                 ./volumes/results/                                    │
│              (final output to user)                                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    External Services                          │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────┐  │   │
│  │  │ SearXNG  │  │ X.com API    │  │ xAI Grok │  │ AWS S3  │  │   │
│  │  │  :8888   │  │ (via x-com)  │  │ (grok)   │  │         │  │   │
│  │  └──────────┘  └──────────────┘  └──────────┘  └─────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### Agent Delegation Protocol

The main profile delegates to specialist profiles in-process via the `delegate-profile` skill:

1. **Main profile** calls the skill's `send_task` (or `send_task_background` for long tasks) with `target_agent_id`, `goal`, and optional `context`/`save_results_to`
2. The skill runs `hermes -p <profile> chat --oneshot` — foreground for the summary, or detached with a log file for background tasks
3. **Target profile** executes the task with its own config, SOUL.md and skills (and OpenCode CLI for `coder`)
4. Foreground: the summary is returned directly; background: `check_task` polls status and returns the log tail
5. Result files are written to `/app/results` and picked up by the main profile

### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/opt/data` | `./volumes/agent` | Main (default) profile workspace (config, SOUL.md, memory, skills) |
| `/opt/data/profiles/{researcher,simple,coder}` | `./volumes/agent/profiles/...` | Secondary profile workspaces (config, SOUL.md, skills, per-profile `.env`) |
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
| `OLLAMA_CONTEXT_LENGTH` | No | Ollama server context length (compose default: 65536). Known tension: `hermes.partial.yml` pins `ollama_num_ctx` to 256000 for main/coder/researcher (65536 for simple), which can exceed this default |
| `HERMES_API_TIMEOUT` | No | Hermes API read timeout in seconds (default: 3600) |
| `HERMES_UID` / `HERMES_GID` | No | Linux hosts: run the container with these UID/GID so `./volumes/*` bind mounts are owned by your user |
| `LOG_LEVEL` | No | Logging level (info, debug, warn, error) |

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs
