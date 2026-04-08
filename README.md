# BOB The Agent

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
cd bob-the-agent-docker
cp .env.template .env
# Edit .env with your configuration
```

### 2. Start Services

```bash
docker compose up -d
```

### 3. Pull a Model

```bash
# Pull the default model
docker exec bob-the-agent-ollama ollama pull qwen3.5:4b
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
| `DEFAULT_PROVIDER` | Primary model provider | `ollama` |
| `OLLAMA_BASE_URL` | Ollama API URL | `http://ollama:11434` |
| `OLLAMA_MODEL` | Default model | `qwen3.5:4b` |
| `OLLAMA_API_KEY` | Ollama API Key | - |
| `DISCORD_BOT_TOKEN` | Discord bot token | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
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

### Web Search

Search multiple sources simultaneously.

```json
{
  "skill": "web-search",
  "parameters": {
    "query": "latest AI developments",
    "sources": ["duckduckgo", "google"],
    "maxResults": 10
  }
}
```

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
├── docker-compose.yml       # Service orchestration
├── dockerfiles/
│   ├── Dockerfile.agent     # Agent container
│   └── Dockerfile.web       # Web interface container
├── src/
│   ├── api/                 # Node.js API server
│   ├── frontend/            # React web interface
│   ├── config/              # OpenClaw & Nginx configuration
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

# Build specific service
docker compose build agent
docker compose build web
```

### Running Tests

```bash
# API tests
cd src/api
npm test

# Frontend tests
cd src/frontend
npm test
```

## Troubleshooting

### Ollama not responding

```bash
# Check if Ollama is running
docker compose logs ollama

# Pull a local model manually
docker exec bob-the-agent-ollama ollama pull qwen3.5:4b
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
