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
│  │  ├─ multiplexed gateway (:8642, loopback-only)              │         │
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
| `ollama` | Local LLM inference engine | 11434 |
| `agent` | Single Hermes Agent container — default profile `main` (orchestrator + Discord) with a gateway multiplexing the researcher/simple/coder profiles | 8642 (loopback-only) |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent` depends on `ollama` (LLM inference, healthy) and `searxng` (started)
- `searxng` depends on `valkey` (caching)

### Agent Profiles

| Profile | Model (default) | Role |
|-------|-------|------|
| `main` | `qwen3.5:2b-q4_K_M` (local, via Ollama) | Main orchestrator — receives tasks, delegates to specialist profiles, provides Discord bot, manages results |
| `researcher` | `qwen3.5:2b-q4_K_M` (local, via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple` | `qwen3.5:2b-q4_K_M` (local, via Ollama) | Simple task handler — web search, summaries, data extraction, S3 uploads |
| `coder` | `qwen3.5:2b-q4_K_M` (local, via Ollama) | Coding specialist — Hermes profile orchestrating OpenCode CLI for software engineering tasks |

Models are changed per profile via `src/agents/{name}/hermes.partial.yml` (see [Configuration Guide](./CONFIGURATION.md)). All profiles currently pin the local default; cloud models (e.g. `kimi-k2.6:cloud`) can be wired the same way after `ollama signin`.

### Coder Profile: Two-Layer Architecture

Unlike the other profiles that use a single Hermes Agent instance, the coder profile uses a **two-layer architecture** where Hermes acts as a supervisor and communication bridge, and OpenCode CLI serves as the coding engine. Both layers live in the same container; OpenCode CLI is installed at image build time (`npm install -g opencode-ai@latest`):

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
│  │  - Uses the profile's model via Ollama                      ││
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

The single container uses a `bootstrap.sh` startup script that provisions the default (main) profile and the three secondary profiles, then execs the long-running gateway:

```
┌──────────────────────────────────────────────────────────────┐
│                    Container Startup Flow                      │
│                                                               │
│  bootstrap.sh (container command; runs as the hermes user)    │
│     │                                                         │
│     ├── 1. Default profile (main)                             │
│     │   ├── config.yaml missing? → generate-config.sh         │
│     │   │   (merge hermes.template.yaml + main partial via    │
│     │   │    merge-yaml.mjs, partial wins)                    │
│     │   └── config.yaml exists? → reconcile with the current  │
│     │       partial (one-time .pre-reconcile.bak snapshot;    │
│     │       partial wins, user-only keys preserved)           │
│     │   └── Copy SOUL.md + skills to /opt/data/               │
│     │                                                         │
│     ├── 2. Secondary profiles (researcher, simple, coder)     │
│     │   Each under /opt/data/profiles/{name}/:                │
│     │   ├── config.yaml (generate or reconcile, as above)     │
│     │   ├── SOUL.md, skills/, profile.yaml (bot roster        │
│     │   │   marker), per-profile .env (chmod 600)             │
│     │   └── Bot Chat session (message_agent path; best-effort)│
│     │                                                         │
│     ├── 3. OpenCode CLI config                                │
│     │   └── env-substitute opencode.template.jsonc →          │
│     │       /opt/data/.config/opencode/opencode.jsonc         │
│     │                                                         │
│     ├── 4. Default Ollama model                               │
│     │   └── Pull the template's local default (~1.6GB) if     │
│     │       missing (best-effort, capped at 30 minutes)       │
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
| `src/config/hermes.template.yaml` | Shared Hermes config template (model defaults, toolsets, agent settings) |
| `src/agents/{name}/hermes.partial.yml` | Per-profile config overrides (model, toolsets, providers) |
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/config/opencode.template.jsonc` | OpenCode CLI config template for coder profile (env-substituted at startup) |
| `src/agents/{name}/IDENTITY.md` | Agent identity metadata |
| `src/agents/{name}/AGENTS.md` | Agent workspace instructions |
| `src/agents/{name}/TOOLS.md` | Agent tool-specific notes |
| `src/scripts/bootstrap.sh` | Container command: provisions all profiles, then execs the gateway |
| `src/scripts/generate-config.sh` | Config generation orchestrator |
| `src/scripts/merge-yaml.mjs` | YAML deep-merge utility (partial wins on overlapping keys) |
| `src/scripts/migrate-volumes.sh` | One-time migration of old per-agent volumes into `./volumes/agent` |
| `src/config/searxng.settings.yml` | SearXNG configuration |

## Skills Architecture

Skills are TypeScript implementations invoked via the `skill-runner.mjs` CLI. Each skill has a `SKILL.md` defining its interface and an `index.ts` with its implementation.

```
src/skills/
├── index.ts                    # Skill registry
├── skill-runner.mjs            # CLI entry point for executing skills
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

Skills are invoked via:
```bash
node /app/scripts/skill-runner.mjs --skill <skill-name> --params '<json-params>'
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Data Flow                                  │
│                                                                      │
│  User → Discord → agent Gateway (:8642, loopback-only)               │
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
│    │   - SOUL.md, AGENTS.md, TOOLS.md                    │          │
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
| `OLLAMA_CONTEXT_LENGTH` | No | Ollama server context length (default: 65536; must stay >= the pinned `model.ollama_num_ctx`) |
| `HERMES_API_TIMEOUT` | No | Hermes API read timeout in seconds (default: 3600; raised for slow CPU inference) |
| `HERMES_UID` / `HERMES_GID` | No | Linux hosts: run the container with these UID/GID so `./volumes/*` bind mounts are owned by your user |
| `LOG_LEVEL` | No | Logging level (info, debug, warn, error) |

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs
