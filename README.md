# BOB The Agent
![Bob The Agent](img/bob-logo-sm.png)

A containerized AI agent orchestrator that runs 24/7 autonomously via Docker/Docker Compose. Built with OpenClaw framework and Ollama for local LLM inference.

## Features

- **Fully Autonomous**: Runs tasks without user interaction
- **Multiple Interfaces**: CLI, Discord bot, and web dashboard
- **Local LLM**: Uses Ollama for cost-effective inference
- **Cloud Fallback**: Supports Anthropic and OpenAI APIs
- **Skills**: Web search, data extraction, math operations, AWS S3, Playwright
- **Docker**: Easy deployment with Docker Compose

## Quick Start

### Prerequisites

- Docker Desktop or Docker Engine + Docker Compose v2
- At least 4GB RAM (8GB recommended)
- Optional: NVIDIA GPU for faster inference

### 1. Clone and Configure

```bash
git clone <repository-url>
cd bob-the-agent
cp .env.template .env
# Edit .env with your configuration
```

### 2. Start Services

```bash
docker compose up -d
```

### 3. Pull Ollama Models

```bash
# Pull the default model
docker exec bob-the-agent-ollama ollama pull qwen3.5:2b-q4_K_M
# Pull all cloud models manifests
docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
...
```

### 4. Sign in to Ollama (First Run)

```bash
# Sign in to Ollama in the ollama container
docker exec -it bob-the-agent-ollama ollama signin
```

### 5. Pair Discord Bot (Optional)

If using Discord bot:

```bash
# Pair Discord bot in the agent container
docker exec -it bob-the-agent openclaw pairing approve discord <pairing-token>
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       Docker Network                    │
├─────────────────┬───────────────────┬───────────────────┤
│                 │                   │                   │
│   Ollama        │   Agent           │   SearXNG         │
│   Port: 11434   │   Port: 18789     │   Port: 8888      │
│                 │                   │                   │
│   LLM Inference │   OpenClaw        │   Search Engine   │
│                 │   Gateway         │                   │
└─────────────────┴───────────────────┴───────────────────┘
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level | `info` |
| `DISCORD_BOT_TOKEN` | Discord bot token | - |
| `SEARXNG_BASE_URL` | SearXNG base url, local or remote | http://searxng:8888 |
| `AWS_S3_BUCKET` | AWS S3 Bucket to upload files to | - |
| `AWS_S3_REGION` | AWS S3 Region | - |
| `AWS_ACCESS_KEY_ID` | AWS S3 Access Key | - |
| `AWS_SECRET_ACCESS_KEY` | AWS S3 Access Key Secret | - |

### Volume Mounts

| Path | Purpose |
|------|---------|
| `./volumes/results` | Task output files |
| `./volumes/user-files` | Input files for tasks |
| `./volumes/data` | Agent internal data |

## Usage

### CLI (via Docker)

```bash
# Connect to agent container
docker exec -it bob-the-agent bash

# Add a task
openclaw task add "Search for latest AI news"

# List tasks
openclaw task list

# Run a task
openclaw task run <task-id>

# Schedule a task
openclaw schedule add --cron "0 9 * * *" "Daily report"
```

### Discord Bot

1. Create a Discord application at https://discord.com/developers/applications
2. Create a bot and copy the token
3. Set `DISCORD_BOT_TOKEN` and `DISCORD_CLIENT_ID` in `.env`
4. Invite the bot to your server
5. Use slash commands: `/task`, `/schedule`, `/status`, `/result`

## Skills

### Data Extraction

Extract data from PDFs, web pages, and DOCX files.

```json
{
  "skill": "data-extraction",
  "parameters": {
    "source": { "type": "url", "value": "https://example.com/data" },
    "outputFormat": "json"
  }
}
```

### Math Operations

Calculate expressions and statistics.

```json
{
  "skill": "math-operations",
  "parameters": {
    "operation": "calculate",
    "expression": "sqrt(2) * pi"
  }
}
```

### AWS S3

Upload files and generate URLs for S3 objects.

```json
{
  "skill": "aws-s3",
  "parameters": {
    "action": "upload",
    "key": "reports/report.pdf",
    "filePath": "/data/report.pdf"
  }
}
```

Actions:
- `upload` - Upload files to S3
- `getUrl` - Generate time-limited presigned URL
- `getPublicUrl` - Generate public URL (no credentials needed)

### Playwright

Browser automation using playwright-cli.

```bash
playwright-cli open https://example.com
playwright-cli screenshot --filename=screenshot.png
playwright-cli close
```

## Development

### Project Structure

```
bob-the-agent-docker/
├── compose.yaml       # Service orchestration
├── dockerfiles/
│   ├── Dockerfile.agent     # Agent container
├── src/
│   ├── agents/              # Agents definitions
│   ├── config/              # OpenClaw configuration
│   ├── scripts/             # Global scripts for agents and containers setup
│   ├── skills/              # Skill implementations
│   └── discord/             # Discord bot
├── volumes/
│   ├── results/             # Task output files
│   ├── user-files/          # Input files
│   └── data/                # Agent data
└── docs/                    # Documentation
```

### Building from Source

```bash
# Build all images
docker compose build
```

## Troubleshooting

### Ollama not responding / timeouts

```bash
# Check if Ollama is running
docker compose logs ollama

# Sign into ollama account for cloud models access
docker exec bob-the-agent-ollama ollama signin

# Pull all models from ollama manually
docker exec bob-the-agent-ollama ollama pull qwen3.5:2b-q4_K_M
docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
```

### Agent container not starting

```bash
# Check logs
docker compose logs agent

# Verify configuration
docker compose config
```

### Search enginge not working

```bash
# Check if searXNG service is running
docker compose ps searxng

# Check searXNG logs
docker compose logs searxng
```

## License

MIT License
