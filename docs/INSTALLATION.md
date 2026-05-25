# Installation Guide

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| CPU | 4 cores | 8 cores |
| RAM | 4 GB | 8+ GB |
| Storage | 20 GB | 50 GB |
| GPU | Optional | NVIDIA with CUDA |

**Note:** The default setup runs 4 agent containers (main, researcher, simple, coder). The coder agent requires more resources (4G reservation, 8G limit) due to running both Hermes Agent and OpenCode CLI. For systems with less RAM, you can comment out `researcher`, `simple-agent`, and `coder` services in `compose.yaml` to run only the main agent (4 GB RAM minimum in that case).

## Prerequisites

### Docker

1. Install Docker Desktop (macOS/Windows) or Docker Engine (Linux)
   - macOS: https://docs.docker.com/desktop/install/mac-install/
   - Windows: https://docs.docker.com/desktop/install/windows-install/
   - Linux: https://docs.docker.com/engine/install/

2. Install Docker Compose v2 (included with Docker Desktop)
   ```bash
   docker compose version
   ```

### GPU Support (Optional)

For NVIDIA GPU acceleration:

1. Install NVIDIA drivers
2. Install NVIDIA Container Toolkit
3. Add GPU configuration to `compose.yaml`:
   ```yaml
   services:
     ollama:
       deploy:
         resources:
           reservations:
             devices:
               - driver: nvidia
                 count: all
                 capabilities: [gpu]
   ```

## Installation Steps

### Step 1: Get the Code

```bash
git clone <repository-url>
cd bob-the-agent
```

### Step 2: Configure Environment

```bash
cp .env.template .env
```

Edit `.env` with your settings:

```env
# Core
LOG_LEVEL=info

# Optional: Cloud Provider (via Ollama signin)
# Sign into Ollama after startup for cloud model access

# Optional: Discord Bot
DISCORD_BOT_TOKEN=your-token
DISCORD_CLIENT_ID=your-client-id

# Optional: External services
SEARXNG_BASE_URL=http://searxng:8888
USER_X_COM_API_TOKEN=your-x-com-token
USER_XAI_SEARCH_API_KEY=your-xai-key

# Optional: AWS S3
USER_AWS_S3_BUCKET=your-bucket
USER_AWS_S3_REGION=us-east-1
USER_AWS_S3_ACCESS_KEY_ID=your-key
USER_AWS_S3_SECRET_ACCESS_KEY=your-secret
```

### Step 3: Build Image & Start Services

All agent services share a single `bob-the-agent:latest` image. Build it before starting:

```bash
# Build the agent image
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .

# Start all services
docker compose up -d

# Check status (should show 8 running containers)
docker compose ps
```

Alternative build options:
```bash
# Build and start in one step
./run-docker.sh

# Pull from registry instead of building locally
docker pull your-registry/bob-the-agent:latest
docker tag your-registry/bob-the-agent:latest bob-the-agent:latest
docker compose up -d
```

Expected containers:
- `bob-the-agent-ollama` — LLM inference
- `bob-the-agent` — Main orchestrator agent
- `bob-the-agent-researcher` — Research specialist agent
- `bob-the-agent-simple` — Simple task handler agent
- `bob-the-agent-coder` — Software engineering specialist agent
- `bob-the-agent-nats` — NATS inter-agent messaging
- `bob-the-agent-searxng` — Web search engine
- `bob-the-agent-valkey` — Cache for SearXNG

### Step 4: Pull Ollama Models

```bash
# Pull the default local model
docker exec bob-the-agent-ollama ollama pull qwen3.5:2b-q4_K_M

# Sign into Ollama for cloud models
docker exec -it bob-the-agent-ollama ollama signin

# Pull cloud model manifests
docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
docker exec bob-the-agent-ollama ollama pull minimax-m2.7:cloud

# Pull coder agent model (required for coder agent)
docker exec bob-the-agent-ollama ollama pull glm-5.1:cloud
```

### Step 5: Verify Installation

```bash
# Check Ollama API
curl http://localhost:11434/api/tags

# Check main agent gateway
curl http://localhost:8642/healthz

# Check SearXNG
curl http://localhost:8888/healthz

# Check coder agent health (if running)
docker compose logs coder --tail 5
```

### Step 6: Pair Discord Bot (Optional)

If using the Discord bot with Hermes Agent:

1. Ensure `DISCORD_BOT_TOKEN` is set in `.env`
2. Restart the main agent: `docker compose restart agent-main`
3. Check logs for Discord connection: `docker compose logs agent-main | grep -i discord`

For detailed Discord setup instructions, see [Discord Setup](./DISCORD_SETUP.md).

## Platform-Specific Notes

### macOS

- Docker Desktop includes all necessary components
- Apple Silicon (M1/M2/M3/M4) works with ARM images
- GPU acceleration not available on Apple Silicon

### Windows

- Use WSL 2 for best performance
- Enable Docker Desktop WSL 2 backend
- GPU support requires WSL 2 with CUDA

### Linux

- Install Docker Engine and Docker Compose separately
- Add user to docker group: `sudo usermod -aG docker $USER`
- NVIDIA GPU requires nvidia-container-toolkit

## Managing Agent Containers

### Run Only Main Agent (Low Resource)

For systems with limited RAM, edit `compose.yaml` and comment out services you don't need. Commenting out the coder agent saves the most resources (8G memory limit):

```yaml
# coder:
#   ...
# researcher:
#   ...
# simple-agent:
#   ...
```

Then start with:
```bash
docker compose up -d
```

### Rebuild After Config Changes

When you change agent configuration files (partials, SOUL.md, etc.), rebuild the image (this affects all agents since they share one image):

```bash
# Rebuild the image
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .

# Restart all services
docker compose up -d
```

## Updating

```bash
# Pull latest changes
git pull

# Rebuild the agent image
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .

# Restart services
docker compose up -d
```

## Uninstalling

```bash
# Stop and remove containers
docker compose down

# Remove volumes (WARNING: deletes all agent data and models)
docker compose down -v

# Remove the agent image
docker rmi bob-the-agent:latest
```

## Next Steps

- Read [Configuration Guide](./CONFIGURATION.md) for detailed configuration options
- Read [Discord Setup](./DISCORD_SETUP.md) for Discord bot configuration
- Read [Troubleshooting](./TROUBLESHOOTING.md) for common issues