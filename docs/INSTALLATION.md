# Installation Guide

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| CPU | 4 cores | 8 cores |
| RAM | 4 GB | 8 GB |
| Storage | 20 GB | 50 GB |
| GPU | Optional | NVIDIA with CUDA |

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
3. Add GPU configuration to docker-compose.yml:
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
cd bob-the-agent-docker
```

### Step 2: Configure Environment

```bash
cp .env.template .env
```

Edit `.env` with your settings:

```env
# Required
LOG_LEVEL=info
DEFAULT_PROVIDER=ollama
OLLAMA_MODEL=llama3.2

# Web Interface
WEB_USERNAME=admin
WEB_PASSWORD=your-secure-password
SESSION_SECRET=your-random-32-char-secret

# Optional: Cloud Providers
ANTHROPIC_API_KEY=your-key
OPENAI_API_KEY=your-key

# Optional: Discord Bot
DISCORD_BOT_TOKEN=your-token
DISCORD_CLIENT_ID=your-client-id
```

### Step 3: Start Services

```bash
# Start all services
docker compose up -d

# Check status
docker compose ps
```

### Step 4: Pull Initial Model

```bash
# Pull default model
docker exec bob-the-agent-ollama ollama pull llama3.2

# Optional: Pull additional models
docker exec bob-the-agent-ollama ollama pull codellama
```

### Step 5: Verify Installation

```bash
# Check Ollama API
curl http://localhost:11434/api/tags

# Check agent health
curl http://localhost:18789/healthz

# Check web interface
curl http://localhost:8080/health
```

## Platform-Specific Notes

### macOS

- Docker Desktop includes all necessary components
- Apple Silicon (M1/M2/M3) works with ARM images
- GPU acceleration not available on Apple Silicon

### Windows

- Use WSL 2 for best performance
- Enable Docker Desktop WSL 2 backend
- GPU support requires WSL 2 with CUDA

### Linux

- Install Docker Engine and Docker Compose separately
- Add user to docker group: `sudo usermod -aG docker $USER`
- NVIDIA GPU requires nvidia-container-toolkit

## Updating

```bash
# Pull latest changes
git pull

# Rebuild containers
docker compose build

# Restart services
docker compose up -d
```

## Uninstalling

```bash
# Stop and remove containers
docker compose down

# Remove volumes
docker compose down -v

# Remove images
docker rmi $(docker images -q 'bob-the-agent*')
```

## Next Steps

- Read [Configuration Guide](./CONFIGURATION.md)
- Read [Discord Setup](./DISCORD_SETUP.md)
- Read [Troubleshooting](./TROUBLESHOOTING.md)