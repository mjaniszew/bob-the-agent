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

# Cloud Providers (Optional)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Discord (Optional)
DISCORD_BOT_TOKEN=
DISCORD_CLIENT_ID=

# AWS S3 (Optional)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
```

### openclaw.template.json

Agent behavior configuration template located at `src/config/openclaw.template.json`.

**How it works:**
1. At container startup, `app-entrypoint.sh` checks if OpenClaw config exists
2. If not, `generate-config.mjs` reads the JSON template
3. Environment variables are substituted (e.g., `${DISCORD_BOT_TOKEN}`)
4. Final config is written to `/home/node/.openclaw/openclaw.json`

The template uses OpenClaw's native JSON format. Only essential configuration is specified; OpenClaw manages defaults for optional settings.

```json
{
  "meta": {
    "lastTouchedVersion": "2026.3.13"
  },
  "auth": {
    "profiles": {
      "ollama:default": {
        "provider": "ollama",
        "mode": "api_key"
      }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "ollama": {
        "baseUrl": "http://ollama:11434",
        "models": [
          { "id": "llama3.2", "contextWindow": 128000 }
        ]
      }
    }
  },
  "gateway": {
    "port": 18789,
    "mode": "local"
  }
}
```

**Environment Variable Substitution:**
Any `${VAR_NAME}` in the template is replaced with the corresponding environment variable value at startup. If a variable is missing, startup fails with an error.

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

### Discord Bot

| Variable | Type | Description |
|----------|------|-------------|
| `DISCORD_BOT_TOKEN` | string | Discord bot token |
| `DISCORD_CLIENT_ID` | string | Discord application ID |

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

## Model Configuration

### Adding Models

1. Pull additional models:
   ```bash
   docker exec bob-the-agent-ollama ollama pull codellama
   ```

2. Configure in `openclaw.template.json`:
   ```json
   "providers": {
     "ollama": {
       "models": [
         { "id": "llama3.2" },
         { "id": "codellama" },
         { "id": "mistral" }
       ]
     }
   }
   ```

### Cloud Provider Setup

1. Set API key:
   ```env
   ANTHROPIC_API_KEY=sk-ant-...
   ```

2. Add provider configuration to `openclaw.template.json`:
   ```json
   "models": {
     "providers": {
       "anthropic": {
         "apiKey": "${ANTHROPIC_API_KEY}",
         "models": [
           { "id": "claude-sonnet-4-6" }
         ]
       }
     }
   }
   ```

3. Switch default provider by setting the primary model:
   ```json
   "agents": {
     "defaults": {
       "model": {
         "primary": "anthropic/claude-sonnet-4-6"
       }
     }
   }
   ```

## Security Considerations

### Password Security

- Use strong passwords (16+ characters)
- Change default credentials immediately

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

Adjust concurrency in the OpenClaw configuration:
```json
"agents": {
  "defaults": {
    "maxConcurrentTasks": 5
  }
}
```