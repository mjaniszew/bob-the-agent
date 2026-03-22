# Architecture Overview

This document describes the architecture of the Mini Agent system.

## Container Architecture

The system runs as multiple Docker containers managed by Docker Compose:

```
┌─────────────────────────────────────────────────────────────┐
│                     Docker Compose                          │
│                                                             │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐               │
│  │ ollama  │    │  agent  │    │   web   │               │
│  │ :11434  │◄───│ OpenClaw │───►│  nginx  │               │
│  │         │    │  :18789  │    │  :8080  │               │
│  └─────────┘    └─────────┘    └─────────┘               │
│       │              │               │                     │
│       ▼              ▼               ▼                     │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                  Docker Volumes                      │  │
│  │  - ollama_data (models)                            │  │
│  │  - ./volumes/data (database, logs)                 │  │
│  │  - ./volumes/results (task outputs)                │  │
│  │  - ./volumes/user-files (input files)              │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Services

| Service | Description | Port |
|---------|-------------|------|
| `ollama` | Local LLM inference engine | 11434 |
| `agent` | OpenClaw-based autonomous agent | 18789 (gateway) |
| `web` | Nginx web interface (optional) | 8080 |

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
├── document-creation/
│   ├── index.ts
│   └── document-creation.md
├── notifications/
│   ├── index.ts
│   └── notifications.md
└── scheduling/
    ├── index.ts
    └── scheduling.md
```

### Current Integration Status

**⚠️ Important:** The skills in `src/skills/` are TypeScript implementations that are NOT currently integrated with the OpenClaw agent.

| Status | Description |
|--------|-------------|
| ✅ Implemented | Skill code exists with proper TypeScript types |
| ⚠️ Not Integrated | Skills lack OpenClaw skill loader mechanism |
| 📋 See Task 3 | `docs/tasks/3-integrate-skills.md` for follow-up work |

These implementations serve as:
1. **Documentation** of expected skill interfaces
2. **Reference implementations** for skill contracts
3. **Basis** for future OpenClaw integration work

## Data Flow

```
┌────────────────────────────────────────────────────────────┐
│                      Data Flow                             │
│                                                            │
│  User → Discord/Web UI → Agent Gateway (18789)             │
│                           │                                │
│                           ▼                                │
│                     OpenClaw Agent                         │
│                           │                                │
│              ┌────────────┼────────────┐                   │
│              ▼            ▼            ▼                   │
│         Skills      Task Queue     Memory                  │
│              │            │            │                   │
│              │            ▼            │                   │
│              │      SQLite DB         │                   │
│              │       (tasks.db)        │                   │
│              │                         │                   │
│              ▼                         ▼                   │
│         Provider API            File Storage               │
│         (Ollama/Cloud)          (/app/workspace)            │
└────────────────────────────────────────────────────────────┘
```

### Storage Locations

| Path (Container) | Path (Host) | Purpose |
|-------------------|-------------|---------|
| `/app/workspace` | `./volumes/workspace` | Working directory for tasks |
| `/app/results` | `./volumes/results` | Task output files |
| `/app/user-files` | `./volumes/user-files` | Input files from user |
| `/app/data` | `./volumes/data` | Database, logs, memory |
| `/root/.ollama` | `ollama_data` volume | Downloaded models |

## Environment Variables

See `.env.template` for all configurable variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | For Discord | Bot token from Discord Developer Portal |
| `OLLAMA_BASE_URL` | No | Defaults to `http://ollama:11434` |

Environment variables are substituted at container startup via the `generate-config.mjs` script.

## Related Documentation

- [Configuration Guide](./CONFIGURATION.md) - Detailed configuration options
- [Installation Guide](./INSTALLATION.md) - Setup instructions
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
- [Skills Documentation](./features/SKILLS.md) - Skill capabilities