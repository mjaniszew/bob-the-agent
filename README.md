# BOB The Agent

A containerized AI agent that runs 24/7 autonomously via Docker/Docker Compose. Built with OpenClaw framework and Ollama for local LLM inference.

## Features

- **Fully Autonomous**: Runs tasks without user interaction
- **Multiple Interfaces**: CLI, Discord bot, and web dashboard
- **Local LLM**: Uses Ollama for cost-effective inference
- **Cloud Fallback**: Supports Anthropic and OpenAI APIs
- **Skills**: Web search, data extraction, math operations, document creation, notifications, scheduling
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

### 3. Access the Dashboard

Open http://localhost:8080 in your browser.

Default credentials:
- Username: `admin`
- Password: `change-me-secure-password`

### 4. Pull a Model

```bash
# Pull the default model
docker exec bob-the-agent-ollama ollama pull llama3.2
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Network                       │
├─────────────────┬─────────────────┬─────────────────────┤
│                 │                 │                     │
│   Ollama        │   Agent         │   Web Interface    │
│   Port: 11434   │   Port: 18789   │   Port: 8080       │
│                 │                 │                     │
│   LLM Inference │   OpenClaw      │   React + Node.js  │
│                 │   Gateway       │   + Nginx          │
└─────────────────┴─────────────────┴─────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level | `info` |
| `DEFAULT_PROVIDER` | Primary model provider | `ollama` |
| `OLLAMA_BASE_URL` | Ollama API URL | `http://ollama:11434` |
| `OLLAMA_MODEL` | Default model | `llama3.2` |
| `WEB_USERNAME` | Web UI username | `admin` |
| `WEB_PASSWORD` | Web UI password | `change-me-secure-password` |
| `DISCORD_BOT_TOKEN` | Discord bot token | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |

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

### Web Dashboard

Access at http://localhost:8080

- View task status and history
- Create and run tasks
- Manage schedules
- Download results
- View system diagnostics

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

### Document Creation

Generate PDF and DOCX documents.

```json
{
  "skill": "document-creation",
  "parameters": {
    "format": "pdf",
    "template": "report",
    "language": "en",
    "content": {
      "title": "Report Title",
      "sections": [...]
    }
  }
}
```

### Notifications

Send notifications via Discord or email.

```json
{
  "skill": "notifications",
  "parameters": {
    "channels": [{ "type": "discord", "webhook_url": "..." }],
    "message": { "title": "Task Complete", "body": "..." }
  }
}
```

### Scheduling

Schedule recurring tasks.

```json
{
  "skill": "scheduling",
  "parameters": {
    "mode": "cron",
    "schedule": { "expression": "0 9 * * *" },
    "task": { "name": "Daily Report", "skill": "web-search", ... }
  }
}
```

## API Reference

### Tasks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tasks` | GET | List all tasks |
| `/api/tasks` | POST | Create a task |
| `/api/tasks/:id` | GET | Get task details |
| `/api/tasks/:id` | DELETE | Delete a task |
| `/api/tasks/:id/run` | POST | Execute a task |

### Schedules

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/schedules` | GET | List all schedules |
| `/api/schedules` | POST | Create a schedule |
| `/api/schedules/:id` | DELETE | Delete a schedule |

### Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get agent status |
| `/api/diagnostics` | GET | Get system diagnostics |

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

# Pull a model manually
docker exec bob-the-agent-ollama ollama pull llama3.2
```

### Agent container not starting

```bash
# Check logs
docker compose logs agent

# Verify configuration
docker compose config
```

### Web interface not accessible

```bash
# Check if web service is running
docker compose ps web

# Check Nginx logs
docker compose logs web
```

## License

MIT License

## Acknowledgments

- [OpenClaw](https://github.com/openclaw/openclaw) - AI agent framework
- [Ollama](https://ollama.com) - Local LLM inference
- [React](https://react.dev) - Frontend framework
- [Express](https://expressjs.com) - Backend framework