# Configuration Guide

## Overview

Bob The Agent uses environment variables and YAML configuration files for customization. The system is built on the Hermes Agent framework with a multi-container architecture where each agent (main, researcher, simple) runs in its own container with its own configuration.

## Configuration Files

### .env

Main configuration file for environment variables.

[.env.template](/.env.template) contains possible environment variables with explanations.

### hermes.template.yaml

Shared Hermes Agent behavior configuration template located at `src/config/hermes.template.yaml`.

**How it works:**
1. At container startup, `hermes-entrypoint.sh` checks if `/opt/data/config.yaml` exists
2. If not, `generate-config.sh` reads the YAML template and agent-specific partial
3. If a partial exists for the agent, `merge-yaml.mjs` deep-merges them (arrays replaced, objects merged)
4. If no partial exists, the template is used as-is
5. Final config is written to `/opt/data/config.yaml`

The template uses Hermes Agent's native YAML format. It contains defaults for model settings, toolsets, agent behavior, terminal configuration, browser settings, checkpoints, and more.

**Example hermes.template.yaml structure:**

```yaml
model:
  default: qwen3.5:2b-q4_K_M
  provider: custom
  base_url: http://ollama:11434/v1
providers: {}
toolsets:
  - hermes-cli
agent:
  max_turns: 90
  gateway_timeout: 1800
  personalities:
    default: You are automation agent...
terminal:
  backend: local
  persistent_shell: true
  lifetime_seconds: 300
```

### Agent Partials (hermes.partial.yml)

Each agent can override specific parts of the shared template via a partial YAML file in `src/agents/{name}/hermes.partial.yml`:

**Main agent** (`src/agents/main/hermes.partial.yml`):
```yaml
model:
  default: kimi-k2.6:cloud
  provider: custom
  base_url: http://ollama:11434/v1
custom_providers:
  - name: ollama/kimi-k2.6:cloud
    base_url: http://ollama:11434/v1
    model: kimi-k2.6:cloud
toolsets:
  - hermes-cli
  - browser
```

**Researcher agent** (`src/agents/researcher/hermes.partial.yml`):
```yaml
model:
  default: kimi-k2.6:cloud
  provider: custom
  base_url: http://ollama:11434/v1
custom_providers:
  - name: ollama/kimi-k2.6:cloud
    base_url: http://ollama:11434/v1
    model: kimi-k2.6:cloud
toolsets:
  - hermes-cli
  - browser
```

**Simple agent** (`src/agents/simple/hermes.partial.yml`):
```yaml
model:
  default: minimax-m2.7:cloud
  provider: custom
  base_url: http://ollama:11434/v1
custom_providers:
  - name: ollama/minimax-m2.7:cloud
    base_url: http://ollama:11434/v1
    model: minimax-m2.7:cloud
toolsets:
  - hermes-cli
  - browser
```

**Merge behavior:**
- Objects are deep-merged (nested keys override individually)
- Arrays are replaced entirely (not concatenated)
- Keys present only in the partial are added
- Keys present only in the template are preserved

### Agent Identity Files

Each agent also has identity and behavioral files that are copied to `/opt/data/` at startup:

| File | Purpose |
|------|---------|
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/agents/{name}/IDENTITY.md` | Agent identity metadata (name, role, emoji) |
| `src/agents/{name}/AGENTS.md` | Agent workspace instructions and conventions |
| `src/agents/{name}/TOOLS.md` | Agent-specific tool notes and tips |

## Environment Variables

### Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | string | `info` | Logging level (debug, info, warn, error) |
| `NODE_ENV` | string | `production` | Node environment |
| `HERMES_YOLO_MODE` | string | `1` | Auto-approve mode (1=enabled) |
| `AGENT_NAME` | string | `main` | Agent identity (main, researcher, simple) |

### Model Providers

| Variable | Type | Description |
|----------|------|-------------|
| `OLLAMA_BASE_URL` | string | Ollama API endpoint (defaults to http://ollama:11434) |

### Search & External Services

| Variable | Type | Description |
|----------|------|-------------|
| `SEARXNG_BASE_URL` | string | SearXNG URL (defaults to http://searxng:8888) |
| `USER_X_COM_API_TOKEN` | string | X.com API token for x-com skill |
| `USER_XAI_SEARCH_API_KEY` | string | xAI API key for grok-search skill |

### Discord Bot

| Variable | Type | Description |
|----------|------|-------------|
| `DISCORD_BOT_TOKEN` | string | Discord bot token |

### AWS S3

| Variable | Type | Description |
|----------|------|-------------|
| `USER_AWS_S3_BUCKET` | string | S3 bucket name |
| `USER_AWS_S3_REGION` | string | S3 region (default: us-east-1) |
| `USER_AWS_S3_ACCESS_KEY_ID` | string | AWS access key |
| `USER_AWS_S3_SECRET_ACCESS_KEY` | string | AWS secret key |

## Docker Compose Configuration

### Service Architecture

The compose file defines 6 services. All agent services share a single `bob-the-agent:latest` image, differentiated by the `AGENT_NAME` environment variable. Common configuration is shared via YAML anchors (`x-agent-image`, `x-agent-env`, `x-agent-healthcheck`, `x-agent-resources`).

```yaml
services:
  ollama:          # LLM inference engine
  agent-main:      # Main orchestrator agent (Hermes + Discord)
  researcher:      # Deep research specialist agent (Hermes)
  simple-agent:    # Simple/cheap task handler (Hermes)
  searxng:         # Privacy-respecting metasearch engine
  valkey:          # Redis-compatible cache for SearXNG
```

### Resource Limits

Resource limits are defined via YAML anchors in `compose.yaml` and shared by all agent services. Adjust as needed:

```yaml
x-agent-resources: &agent-resources
  reservations:
    cpus: 2
    memory: 2G
  limits:
    memory: 4G
```

### Volume Mounts

| Host Path | Container Path | Service | Purpose |
|-----------|---------------|---------|---------|
| `./volumes/agent-main` | `/opt/data` | agent-main | Main agent workspace, config, memory, skills |
| `./volumes/agent-researcher` | `/opt/data` | researcher | Researcher agent workspace |
| `./volumes/agent-simple` | `/opt/data` | simple-agent | Simple agent workspace |
| `./volumes/results` | All agents | Final task output files |
| `ollama_data` | `/root/.ollama` | ollama | Downloaded models |
| `searxng_config` | `/etc/searxng/` | searxng | SearXNG configuration |
| `searxng_data` | `/var/cache/searxng/` | searxng | SearXNG cache |
| `valkey_data` | `/data` | valkey | Valkey/Redis data |

### Ports

| Host Port | Container Port | Service |
|-----------|---------------|---------|
| `11434` | `11434` | Ollama API |
| `8642` | `8642` | agent-main Gateway |
| `8101` | `8642` | researcher Gateway |
| `8102` | `8642` | simple-agent Gateway |
| `8888` | `8888` | SearXNG |

## Model Configuration

### Changing Agent Models

Each agent's model is configured via its `hermes.partial.yml` file:

1. Edit the partial for the agent you want to change, e.g. `src/agents/main/hermes.partial.yml`:

```yaml
model:
  default: your-model-name:cloud
  provider: custom
  base_url: http://ollama:11434/v1
custom_providers:
  - name: ollama/your-model-name:cloud
    base_url: http://ollama:11434/v1
    model: your-model-name:cloud
```

2. Rebuild the image and restart the containers (rebuild affects all agents since they share one image):
```bash
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .
docker compose up -d
```

### Adding Models to Ollama

1. Pull additional models:
   ```bash
   docker exec bob-the-agent-ollama ollama pull your-model
   ```

2. Reference them in agent partials via `custom_providers`.

### Cloud Provider Setup

The system uses Ollama as a proxy for both local and cloud models. Cloud models are accessed through Ollama's cloud model support:

1. Sign into Ollama in the container:
   ```bash
   docker exec -it bob-the-agent-ollama ollama signin
   ```

2. Pull cloud model manifests:
   ```bash
   docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
   ```

3. Reference in agent partials as shown above.

## Security Considerations

### Password Security

- Use strong passwords (16+ characters)
- Change default credentials immediately

### Network Security

- Use HTTPS in production
- Restrict CORS origins
- Use Docker network isolation (agents communicate on internal `bob-the-agent-network`)

### API Keys

- Never commit API keys to git
- Use environment variables (`.env` file)
- Rotate keys periodically

## Performance Tuning

### Memory Allocation

For limited RAM, you can reduce the number of running agent containers:

```yaml
# Comment out researcher and simple-agent in compose.yaml
# to only run the main agent
```

Or reduce memory limits:

```yaml
agent-main:
  deploy:
    resources:
      limits:
        memory: 2G
```

### Model Selection

- Use cloud models (e.g., `kimi-k2.6:cloud`) for best quality
- Use `minimax-m2.7:cloud` for cheaper simple tasks
- Use local models (e.g., `qwen3.5:2b-q4_K_M`) as fallback or for offline operation
- The default template model `qwen3.5:2b-q4_K_M` is overridden by each agent's partial

### Agent Count

The default setup runs 3 agent containers. For resource-constrained environments:
- Run only `agent-main` for basic operation (comment out `researcher` and `simple-agent` in compose.yaml)
- Reduce CPU/memory reservations accordingly