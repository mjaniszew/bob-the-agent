# Installation Guide

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| CPU | 4 cores | 8 cores |
| RAM | 4 GB | 8+ GB |
| Storage | 20 GB | 50 GB |
| GPU | Optional | NVIDIA with CUDA |

**Note:** The default setup runs 4 containers: one `agent` container (all agent profiles — main orchestrator plus multiplexed researcher/simple/coder) plus ollama, searxng, and valkey. For systems with less RAM, reduce the `agent` container's memory limits in `compose.yaml`.

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

If you have runtime data from a previous per-agent-container install (`volumes/agent-main`, `volumes/agent-researcher`, ...), migrate it into the new single-volume layout before the first start (fresh installs can skip this):

```bash
bash src/scripts/migrate-volumes.sh volumes volumes/agent
```

All agent profiles run in a single `bob-the-agent:latest` image. Build it before starting:

```bash
# Build the agent image
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .

# Start all services
docker compose up -d

# Check status (4 containers)
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
- `bob-the-agent` — Single Hermes Agent container (default profile `main` plus multiplexed researcher/simple/coder profiles under `/opt/data/profiles/`)
- `bob-the-agent-searxng` — Web search engine
- `bob-the-agent-valkey` — Cache for SearXNG

### Step 4: Ollama Models

The default local model is pulled automatically by bootstrap on first boot (~1.6GB download, can take longer than the health start_period on the very first boot). Optional, for cloud models (user choice — sign-in is not required for local inference):

```bash
# Sign into Ollama for cloud models (optional)
docker exec -it bob-the-agent-ollama ollama signin

# Pull cloud model manifests referenced in your profile partials
docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
```

### Step 5: Verify Installation

```bash
# Check Ollama API
curl http://localhost:11434/api/tags

# Check main agent gateway
curl http://localhost:8642/healthz

# Check SearXNG
curl http://localhost:8888/healthz

# Check agent logs (all profiles)
docker compose logs agent --tail 5
```

### Step 6: Pair Discord Bot (Optional)

If using the Discord bot with Hermes Agent:

1. Ensure `DISCORD_BOT_TOKEN` is set in `.env`
2. Restart the agent: `docker compose restart agent`
3. Check logs for Discord connection: `docker compose logs agent | grep -i discord`

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

## Managing the Agent Container

### Low Resource

All profiles run inside the single `agent` container — there are no per-agent services to disable. For systems with limited RAM, reduce the container's memory limits in `compose.yaml`:

```yaml
agent:
  deploy:
    resources:
      limits:
        memory: 4G
```

### Rebuild After Config Changes

When you change profile configuration files (partials, SOUL.md, etc.), rebuild the image (all profiles share one image):

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
