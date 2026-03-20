# Configuration Guide

## Overview

Mini Agent uses environment variables and YAML configuration files for customization.

## Configuration Files

### .env

Main configuration file for environment variables.

```env
# Agent Settings
LOG_LEVEL=info
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama3.2

# Web Interface
WEB_USERNAME=admin
WEB_PASSWORD=your-secure-password
SESSION_SECRET=your-random-32-char-secret
ALLOWED_ORIGINS=http://localhost:8080

# Cloud Providers (Optional)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Discord (Optional)
DISCORD_BOT_TOKEN=
DISCORD_CLIENT_ID=

# Notifications (Optional)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
NOTIFICATION_EMAIL=
```

### openclaw.yaml

Agent behavior configuration located at `src/config/openclaw.yaml`.

```yaml
agent:
  name: "MiniAgent"
  description: "Autonomous AI agent running 24/7 in Docker container"
  autonomous: true
  yolo: true

  heartbeat:
    enabled: true
    interval: 60000

  memory:
    type: "file"
    path: "/app/data/memory"

  workspace:
    path: "/app/workspace"
    resultsPath: "/app/results"
    userFilesPath: "/app/user-files"

providers:
  ollama:
    enabled: true
    baseUrl: "http://ollama:11434"
    defaultModel: "llama3.2"
    models:
      - name: "llama3.2"
        alias: "default"
      - name: "llama3.2:1b"
        alias: "fast"
    timeout: 120000

tasks:
  storage:
    type: "sqlite"
    path: "/app/data/tasks.db"

logging:
  level: "${LOG_LEVEL:info}"
  format: "json"
```

## Environment Variables

### Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | string | `info` | Logging level (debug, info, warn, error) |
| `DEFAULT_PROVIDER` | string | `ollama` | Default model provider |
| `NODE_ENV` | string | `production` | Node environment |

### Model Providers

| Variable | Type | Description |
|----------|------|-------------|
| `OLLAMA_BASE_URL` | string | Ollama API endpoint |
| `OLLAMA_MODEL` | string | Default Ollama model |
| `ANTHROPIC_API_KEY` | string | Anthropic API key |
| `OPENAI_API_KEY` | string | OpenAI API key |

### Web Interface

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `WEB_USERNAME` | string | `admin` | Web UI username |
| `WEB_PASSWORD` | string | - | Web UI password |
| `SESSION_SECRET` | string | - | JWT signing secret |
| `ALLOWED_ORIGINS` | string | - | CORS origins (comma-separated) |
| `JWT_EXPIRATION` | number | `86400` | Token expiry in seconds |

### Discord Bot

| Variable | Type | Description |
|----------|------|-------------|
| `DISCORD_BOT_TOKEN` | string | Discord bot token |
| `DISCORD_CLIENT_ID` | string | Discord application ID |

### Notifications

| Variable | Type | Description |
|----------|------|-------------|
| `AWS_ACCESS_KEY_ID` | string | AWS access key for SES |
| `AWS_SECRET_ACCESS_KEY` | string | AWS secret key |
| `AWS_REGION` | string | AWS region |
| `NOTIFICATION_EMAIL` | string | Notification recipient email |

## Docker Compose Configuration

### Service Limits

Adjust resource limits in `docker-compose.yml`:

```yaml
services:
  ollama:
    deploy:
      resources:
        limits:
          memory: 8G
        reservations:
          memory: 2G

  agent:
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 512M
```

### Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./volumes/results` | `/app/results` | Task output files |
| `./volumes/user-files` | `/app/user-files` | Input files |
| `./volumes/data` | `/app/data` | Agent database, logs |
| `ollama_data` | `/root/.ollama` | Model storage |

### Ports

| Host Port | Container Port | Service |
|-----------|---------------|---------|
| `11434` | `11434` | Ollama API |
| `18789` | `18789` | Agent Gateway |
| `8080` | `80` | Web Interface |

## Model Configuration

### Adding Models

1. Pull additional models:
   ```bash
   docker exec mini-agent-ollama ollama pull codellama
   ```

2. Configure in `openclaw.yaml`:
   ```yaml
   providers:
     ollama:
       models:
         - name: "llama3.2"
           alias: "default"
         - name: "codellama"
           alias: "code"
         - name: "mistral"
           alias: "fast"
   ```

### Cloud Provider Setup

1. Set API key:
   ```env
   ANTHROPIC_API_KEY=sk-ant-...
   ```

2. Enable in configuration:
   ```yaml
   providers:
     anthropic:
       enabled: true
       defaultModel: "claude-sonnet-4-6"
   ```

3. Switch default provider:
   ```env
   DEFAULT_PROVIDER=anthropic
   ```

## Security Considerations

### Password Security

- Use strong passwords (16+ characters)
- Change default credentials immediately
- Use `SESSION_SECRET` for JWT signing

### Network Security

- Use HTTPS in production
- Restrict CORS origins
- Use Docker network isolation

### API Keys

- Never commit API keys to git
- Use environment variables
- Rotate keys periodically

## Performance Tuning

### Memory Allocation

For limited RAM:
```yaml
ollama:
  deploy:
    resources:
      limits:
        memory: 4G
```

### Model Selection

- Use smaller models for faster inference
- `llama3.2:1b` for quick tasks
- `llama3.2` for complex tasks

### Parallel Tasks

Adjust concurrency in agent configuration:
```yaml
agent:
  maxConcurrentTasks: 5
```