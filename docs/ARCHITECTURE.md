# Architecture Overview

This document describes the architecture of Bob The Agent system.

## Container Architecture

The system runs as multiple Docker containers managed by Docker Compose. Each specialized agent runs in its own container with its own Hermes Agent instance, sharing a common Ollama model provider and SearXNG search engine. All agent services use a single pre-built `bob-the-agent:latest` image, differentiated by the `AGENT_NAME` environment variable. Common configuration (image, env, healthcheck, resources) is shared via YAML anchors.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Docker Compose                                │
│                                                                         │
│  ┌───────────┐                                                           │
│  │  ollama   │                                                           │
│  │  :11434   │                                                           │
│  │           │                                                           │
│  │  LLM      │                                                           │
│  │  Inference│                                                           │
│  └─────┬─────┘                                                           │
│        │                                                                 │
│   ┌────┴────────────────────────────────────────────┐                   │
│   │         All agents depend on Ollama              │                   │
│   │                                                   │                   │
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │                   │
│   │  │ agent-main  │ │ researcher  │ │simple-agent │ │    coder     │ │                   │
│   │  │  :8642      │ │             │ │             │ │ Hermes +     │ │                   │
│   │  │             │ │             │ │             │ │ OpenCode CLI │ │                   │
│   │  │ Hermes      │ │ Hermes      │ │ Hermes      │ │ Coding       │ │                   │
│   │  │ Orchestrator│ │ Research    │ │ Simple      │ │ Specialist   │ │                   │
│   │  │ + Discord   │ │ Specialist  │ │ Tasks       │ │              │ │                   │
│   │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ │                   │
│   └─────────┼───────────────┼───────────────┼───────────┼─────────────┘
│             │               │               │                              │
│             │    NATS Inter-Agent Messaging                                  │
│             │    (agent.{id}.tasks / agent.{id}.results)                    │
│             │               │               │                              │
│        ┌────┴───────────────┴───────────────┴────────────┘                        │
│        │               NATS Server                │                        │
│        │               :4222 / :8222              │                        │
│        └─────────────────────────────────────────┘                        │
│                                                                         │
│   ┌─────────┴───────────────┴───────────────┴─────────┐                   │
│   │              SearXNG + Valkey                     │                   │
│   │  ┌─────────────┐        ┌─────────────┐           │                   │
│   │  │  searxng    │◄───────│   valkey    │           │                   │
│   │  │   :8888     │        │   :6379     │           │                   │
│   │  │ Web Search  │        │ Cache       │           │                   │
│   │  └─────────────┘        └─────────────┘           │                   │
│   └───────────────────────────────────────────────────┘                   │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                        Docker Volumes                             │   │
│  │  - ollama_data (models)                                           │   │
│  │  - searxng_config, searxng_data (search engine)                  │   │
│  │  - valkey_data (cache)                                           │   │
│  │  - ./volumes/agent-main (main agent workspace & config)         │   │
│  │  - ./volumes/agent-researcher (researcher workspace & config)    │   │
│  │  - ./volumes/agent-simple (simple agent workspace & config)     │   │
│  │  - ./volumes/results (task outputs)                              │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Inter-Agent Communication

Agents communicate across containers via **NATS messaging**. Each agent runs a `register-nats.py` background listener that subscribes to NATS subjects for task delegation and result reporting:

- **`agent.{target_id}.tasks`** — Send a task to a specific agent
- **`agent.{target_id}.results`** — Send a result back to the originating agent

The `agent-to-agent` skill provides the sending interface, while `register-nats.py` handles receiving and executing tasks via Hermes one-shot mode.

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | Local LLM inference engine | 11434 |
| `agent-main` | Hermes Agent — main orchestrator with Discord bot | 8642 |
| `researcher` | Hermes Agent — deep research specialist | |
| `simple-agent` | Hermes Agent — simple/cheap task handler | |
| `coder` | Hermes Agent + OpenCode CLI — software engineering specialist | |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |
| `nats` | NATS messaging server for inter-agent communication | 4222/8222 |

### Service Dependencies

- `agent-main` depends on `ollama` (LLM inference)
- `researcher` depends on `ollama` (LLM inference)
- `simple-agent` depends on `ollama` (LLM inference)
- `coder` depends on `ollama` (LLM inference for both Hermes and OpenCode)
- `searxng` depends on `valkey` (caching)
- All agents connect to `searxng` for web search capabilities

### Agent Roles

| Agent | Model | Role |
|-------|-------|------|
| `agent-main` | `kimi-k2.6:cloud` (via Ollama) | Main orchestrator — receives tasks, delegates to specialist agents, provides Discord bot, manages results |
| `researcher` | `kimi-k2.6:cloud` (via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple-agent` | `minimax-m2.7:cloud` (via Ollama) | Simple task handler — lightweight, cost-effective model for straightforward tasks |
| `coder` | `glm-5.1:cloud` (via Ollama) | Coding specialist — Hermes Agent orchestrating OpenCode CLI for software engineering tasks |

## Configuration Flow

Each agent container uses a two-stage startup process that merges a shared Hermes template with agent-specific overrides:

```
┌──────────────────────────────────────────────────────────────┐
│                    Container Startup Flow                      │
│                                                               │
│  1. hermes-entrypoint.sh                                     │
│     ├── Check: /opt/data/config.yaml exists?                  │
│     │   ├── YES → Skip config generation                     │
│     │   └── NO  → Run generate-config.sh                     │
│     │              │                                          │
│     ├── Check: /opt/data/SOUL.md exists?                      │
│     │   └── NO  → Copy from /app/agents/$AGENT_NAME/SOUL.md  │
│     │                                                         │
│     └── Copy skills to /opt/data/skills/                     │
│                                                               │
│  2. generate-config.sh                                       │
│     ├── Read hermes.template.yaml (shared base config)       │
│     ├── Check for hermes.partial.yml (per-agent overrides)   │
│     │   ├── EXISTS → Merge with merge-yaml.mjs              │
│     │   └── MISSING → Use template as-is                    │
│     └── Write to /opt/data/config.yaml                       │
│                                                               │
│  3. Hermes Agent starts                                      │
│     └── Uses merged config from /opt/data/config.yaml        │
└──────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `src/config/hermes.template.yaml` | Shared Hermes config template (model defaults, toolsets, agent settings) |
| `src/agents/{name}/hermes.partial.yml` | Per-agent config overrides (model, toolsets, providers) |
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/config/opencode.template.json` | OpenCode CLI config template for coder agent (env-substituted at startup) |
| `src/agents/{name}/IDENTITY.md` | Agent identity metadata |
| `src/agents/{name}/AGENTS.md` | Agent workspace instructions |
| `src/agents/{name}/TOOLS.md` | Agent tool-specific notes |
| `src/scripts/hermes-entrypoint.sh` | Container entry point script |
| `src/scripts/generate-config.sh` | Config generation orchestrator |
| `src/scripts/merge-yaml.mjs` | YAML deep-merge utility |
| `src/scripts/hermes-cmd.sh` | Hermes command wrapper |
| `src/scripts/coder-entrypoint.sh` | Coder agent entry point (Hermes setup + OpenCode CLI install + config) |
| `src/scripts/register-nats.py` | NATS background listener for inter-agent messaging |
| `src/config/searxng.settings.yml` | SearXNG configuration |

## Skills Architecture

Skills are TypeScript implementations invoked via the `skill-runner.mjs` CLI. Each skill has a `SKILL.md` defining its interface and an `index.ts` with its implementation.

```
src/skills/
├── index.ts                    # Skill registry
├── skill-runner.mjs            # CLI entry point for executing skills
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
│   └── index.ts
└── math-operations/
    └── index.ts
```

Skills are compiled during Docker build (`npm run build`) and copied to `/opt/data/skills/` at container startup. The Hermes agent accesses them via its skills system.

### Skill Integration Status

| Skill | Status | Description |
|-------|--------|-------------|
| searxng-web-search | ✅ Active | Web search via local SearXNG instance (free, no API keys) |
| grok-search | ✅ Active | X.com search via xAI Grok's x_search tool (fallback for x-com) |
| x-com | ✅ Active | Direct X.com (Twitter) API access for posts, users, timelines |
| aws-s3 | ✅ Active | Upload files and generate presigned URLs for S3 |
| data-extraction | ✅ Implemented | Extract structured data from websites/documents |
| math-operations | ✅ Implemented | Mathematical calculations and operations |
| agent-to-agent | ✅ Active | Inter-agent messaging via NATS for cross-container task delegation |

Skills are invoked via:
```bash
node /app/scripts/skill-runner.mjs --skill <skill-name> --params '<json-params>'
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Data Flow                                  │
│                                                                      │
│  User → Discord → agent-main Gateway (:8642)                        │
│                        │                                             │
│                        ▼                                             │
│                  Main Agent (Orchestrator)                            │
│                        │                                             │
│           ┌────────────┼──────────────┐                              │
│           │            │              │                               │
│           ▼            ▼              ▼                               │
│     delegate_task  NATS messaging  direct execution                  │
│     (in-process)  (cross-container)                                 │
│           │            │                                              │
│           ▼            ▼                                              │
│     researcher    simple-agent                                       │
│     (deep research) (simple tasks)                                    │
│           │            │                                              │
│           ▼            ▼                                              │
│    ┌─────────────────────────────────────┐                            │
│    │   /opt/data/ (agent workspace)     │                            │
│    │   - SOUL.md, AGENTS.md, TOOLS.md   │                            │
│    │   - memory/ (daily notes)          │                            │
│    │   - config.yaml (Hermes config)    │                            │
│    │   - skills/                         │                            │
│    │   - nats-messages/ (inter-agent)    │                            │
│    └─────────────────────────────────────┘                            │
│                        │                                             │
│                        ▼                                             │
│                 ./volumes/results/                                    │
│              (final output to user)                                  │
│                 /opt/data/ (agent workspace output)                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                    External Services                          │    │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────┐  │    │
│  │  │ SearXNG  │  │ X.com API    │  │ xAI Grok │  │ AWS S3  │  │    │
│  │  │  :8888   │  │ (via x-com)  │  │ (grok)   │  │         │  │    │
│  │  └──────────┘  └──────────────┘  └──────────┘  └─────────┘  │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### Agent Delegation Protocol

Hermes Agent supports two delegation mechanisms:

**In-process delegation** (within same container):
1. **Main agent** calls `delegate_task` with goal, context, and toolset specifications
2. **Sub-agent** executes in isolated context with its own terminal session
3. **Sub-agent** returns summary to main agent

**Cross-container delegation** (via NATS messaging):
1. **Main agent** uses `agent-to-agent` skill to send a task to a target agent via NATS
2. **Message** is published to `agent.{target_id}.tasks` subject
3. **Target agent's** `register-nats.py` listener receives the message
4. **Target agent** executes the task via Hermes one-shot mode (`hermes -z "<prompt>"`)
5. **Target agent** publishes result to `agent.{sender_id}.results` subject
6. **Main agent** checks for results via `agent-to-agent` skill's `check_messages` action

### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/opt/data` | `./volumes/agent-main` | Main agent workspace (config, SOUL.md, memory, skills) |
| `/opt/data` | `./volumes/agent-researcher` | Researcher agent workspace |
| `/opt/data` | `./volumes/agent-simple` | Simple agent workspace |
| `/opt/data` | `./volumes/agent-coder` | Coder agent workspace (config, SOUL.md, memory, skills) |
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
| `AGENT_NAME` | No | Agent identity name (main, researcher, simple) |
| `HERMES_YOLO_MODE` | No | Auto-approve mode (1=enabled, 0=manual approval) |
| `LOG_LEVEL` | No | Logging level (info, debug, warn, error) |
| `NATS_URL` | No | NATS server URL (default: nats://nats:4222) |
| `NATS_TASK_TIMEOUT` | No | Task execution timeout in seconds (default: 600) |

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs