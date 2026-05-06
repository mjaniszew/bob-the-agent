# Architecture Overview

This document describes the architecture of Bob The Agent system.

## Container Architecture

The system runs as multiple Docker containers managed by Docker Compose. Each specialized agent runs in its own container with its own Hermes Agent instance, sharing a common Ollama model provider and SearXNG search engine.

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
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │                   │
│   │  │ agent-main  │ │ researcher  │ │simple-agent │ │                   │
│   │  │  :8642      │ │  :8101      │ │  :8102      │ │                   │
│   │  │             │ │             │ │             │ │                   │
│   │  │ Hermes      │ │ Hermes      │ │ Hermes      │ │                   │
│   │  │ Orchestrator│ │ Research    │ │ Simple      │ │                   │
│   │  │ + Discord   │ │ Specialist  │ │ Tasks       │ │                   │
│   │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ │                   │
│   └─────────┼───────────────┼───────────────┼─────────┘                   │
│             │               │               │                              │
│             │    Delegation via Hermes delegate_task                      │
│             │               │               │                              │
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

**Upcoming:** NATS container for inter-agent messaging and task delegation between containers. Currently, agents communicate via Hermes built-in `delegate_task` tool within the same container.

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | Local LLM inference engine | 11434 |
| `agent-main` | Hermes Agent — main orchestrator with Discord bot | 8642 |
| `researcher` | Hermes Agent — deep research specialist | 8101 |
| `simple-agent` | Hermes Agent — simple/cheap task handler | 8102 |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent-main` depends on `ollama` (LLM inference)
- `researcher` depends on `ollama` (LLM inference)
- `simple-agent` depends on `ollama` (LLM inference)
- `searxng` depends on `valkey` (caching)
- All agents connect to `searxng` for web search capabilities

### Agent Roles

| Agent | Model | Role |
|-------|-------|------|
| `agent-main` | `kimi-k2.6:cloud` (via Ollama) | Main orchestrator — receives tasks, delegates to specialist agents, provides Discord bot, manages results |
| `researcher` | `kimi-k2.6:cloud` (via Ollama) | Research specialist — performs deep research, analysis, cross-referencing of sources |
| `simple-agent` | `minimax-m2.7:cloud` (via Ollama) | Simple task handler — lightweight, cost-effective model for straightforward tasks |

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
| `src/agents/{name}/IDENTITY.md` | Agent identity metadata |
| `src/agents/{name}/AGENTS.md` | Agent workspace instructions |
| `src/agents/{name}/TOOLS.md` | Agent tool-specific notes |
| `src/scripts/hermes-entrypoint.sh` | Container entry point script |
| `src/scripts/generate-config.sh` | Config generation orchestrator |
| `src/scripts/merge-yaml.mjs` | YAML deep-merge utility |
| `src/scripts/hermes-cmd.sh` | Hermes command wrapper |
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
│     delegate_task  delegate_task  direct execution                   │
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
│    └─────────────────────────────────────┘                            │
│                        │                                             │
│                        ▼                                             │
│                 ./volumes/results/                                    │
│              (final output to user)                                  │
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

Hermes Agent uses the built-in `delegate_task` tool for sub-agent delegation:

1. **Main agent** receives task and decides whether to delegate or handle directly
2. **Delegation** — Main agent calls `delegate_task` with goal, context, and toolset specifications
3. **Sub-agent** executes in isolated context with its own terminal session
4. **Sub-agent** writes results to workspace files
5. **Sub-agent** returns summary to main agent (only the summary, not full data)
6. **Main agent** processes results and writes final output to `/app/results/`

### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/opt/data` | `./volumes/agent-main` | Main agent workspace (config, SOUL.md, memory, skills) |
| `/opt/data` | `./volumes/agent-researcher` | Researcher agent workspace |
| `/opt/data` | `./volumes/agent-simple` | Simple agent workspace |
| `/app/results` | `./volumes/results` | Final task output files |
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

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs) - Official Hermes Agent docs