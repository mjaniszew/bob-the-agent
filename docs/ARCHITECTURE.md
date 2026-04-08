# Architecture Overview

This document describes the architecture of the Mini Agent system.

## Container Architecture

The system runs as multiple Docker containers managed by Docker Compose:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Docker Compose                                │
│                                                                     │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │ ollama  │    │  agent  │    │ searxng │    │ valkey  │         │
│  │ :11434  │◄───│ OpenClaw │───►│ :8888   │◄───│ :6379   │         │
│  │         │    │  :18789  │    │         │    │         │         │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘         │
│       │              │              │              │               │
│       ▼              ▼              ▼              ▼               │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                      Docker Volumes                          │  │
│  │  - ollama_data (models)                                      │  │
│  │  - searxng_config, searxng_data (search engine)              │  │
│  │  - valkey_data (cache)                                       │  │
│  │  - ./volumes/data (database, logs)                          │  │
│  │  - ./volumes/results (task outputs)                          │  │
│  │  - ./volumes/user-files (input files)                       │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | Local LLM inference engine | 11434 |
| `agent` | OpenClaw-based autonomous agent | 18789 (gateway) |
| `searxng` | Privacy-respecting metasearch engine | 8888 |
| `valkey` | Redis-compatible cache for SearXNG | 6379 |

### Service Dependencies

- `agent` depends on `ollama` (LLM inference)
- `searxng` depends on `valkey` (caching)
- `agent` connects to `searxng` for web search capabilities

## Configuration Flow

The agent container uses a two-stage startup process:

```
┌──────────────────────────────────────────────────────────┐
│                 Container Startup Flow                     │
│                                                            │
│  1. app-entrypoint.sh                                     │
│     └── Check: /home/node/.openclaw/openclaw.json exists? │
│         │                                                  │
│         ├── YES → Skip config generation                  │
│         │                                                  │
│         └── NO  → Run generate-config.mjs                 │
│                    │                                       │
│  2. generate-config.mjs                                   │
│     ├── Read openclaw.template.json                       │
│     ├── Substitute ${VAR_NAME} with env vars              │
│     ├── Write to /home/node/.openclaw/openclaw.json       │
│     └── Continue to agent startup                         │
│                                                            │
│  3. OpenClaw Agent starts                                 │
│     └── Uses generated config from /home/node/.openclaw/   │
└──────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `src/config/openclaw.template.json` | Source template with `${VAR}` placeholders |
| `src/scripts/app-entrypoint.sh` | Entry point script that checks for existing config |
| `src/scripts/generate-config.mjs` | Generates runtime config from template |
| `/home/node/.openclaw/openclaw.json` | Generated config (inside container) |

## Skills Architecture

The agent includes skill implementations in `src/skills/`:

```
src/skills/
├── index.ts              # Registry and executeSkill()
├── web-search/
│   ├── index.ts
│   └── web-search.md     # Skill documentation
├── data-extraction/
│   ├── index.ts
│   └── data-extraction.md
├── math-operations/
│   ├── index.ts
│   └── math-operations.md
```

### Current Integration Status

**⚠️ Important:** The skills in `src/skills/` are TypeScript implementations that are NOT currently integrated with the OpenClaw agent.

**Current Integration Status:**

| Skill | Status | Description |
|-------|--------|-------------|
| data-extraction | ✅ Implemented | Extract structured data from websites/documents |
| math-operations | ✅ Implemented | Mathematical calculations and operations |
| x-com | ✅ Implemented | X.com (Twitter) API integration for posts and searches |
| aws-s3 | ✅ Implemented | Upload files and generate presigned URLs |

These implementations serve as:
1. **Documentation** of expected skill interfaces
2. **Reference implementations** for skill contracts
3. **Basis** for future OpenClaw integration work

## Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                        Data Flow                              │
│                                                              │
│  User → Discord → Agent Gateway (18789)                      │
│                           │                                  │
│                           ▼                                  │
│                     OpenClaw Agent                           │
│                           │                                  │
│              ┌────────────┼────────────┐                     │
│              ▼            ▼            ▼                     │
│         Skills      Task Queue     Memory                     │
│              │            │            │                     │
│              │            ▼            │                     │
│              │      SQLite DB         │                     │
│              │       (tasks.db)        │                     │
│              │                         │                     │
│              ▼                         ▼                     │
│         Provider API            File Storage                 │
│         (Ollama/Cloud)          (/app/workspace)              │
│              │                                                │
│              ▼                                                │
│         ┌────────────────────────────────────┐               │
│         │         External Services          │               │
│         │  ┌─────────┐  ┌───────────────┐  │               │
│         │  │ SearXNG │  │ X.com API     │  │               │
│         │  │ :8888   │  │ (via x-com)   │  │               │
│         │  └─────────┘  └───────────────┘  │               │
│         │  ┌─────────┐  ┌───────────────┐  │               │
│         │  │ AWS S3  │  │ Other APIs    │  │               │
│         │  └─────────┘  └───────────────┘  │               │
│         └────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────┘
```

### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/app/workspace` | `agent_workspace` volume | Working directory for tasks |
| `/app/results` | `./volumes/results` | Task output files |
| `/app/user-files` | `./volumes/user-files` | Input files from user |
| `/app/data` | `./volumes/data` | Database, logs, memory |
| `/root/.ollama` | `ollama_data` volume | Downloaded models |
| `/etc/searxng` | `searxng_config` volume | SearXNG configuration |
| `/var/cache/searxng` | `searxng_data` volume | SearXNG cache |
| `/data` | `valkey_data` volume | Valkey/Redis data |

## Environment Variables

See `.env.template` for all configurable variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OLLAMA_API_KEY` | No | Ollama API key for cloud models |
| `DISCORD_BOT_TOKEN` | For Discord | Bot token from Discord Developer Portal |
| `SEARXNG_BASE_URL` | No | SearXNG URL (defaults to http://searxng:8888) |
| `X_COM_API_TOKEN` | For X.com | X.com API token for x-com skill |
| `AWS_ACCESS_KEY_ID` | For AWS S3 | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | For AWS S3 | AWS secret key |
| `AWS_S3_BUCKET` | For AWS S3 | S3 bucket name |
| `AWS_S3_REGION` | For AWS S3 | S3 bucket region |

Environment variables are substituted at container startup via the `generate-config.mjs` script.

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Skills Documentation](./features/SKILLS.md) - Skill capabilities