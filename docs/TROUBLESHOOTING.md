# Troubleshooting Guide

## Common Issues

### Container Issues

#### Containers Won't Start

**Symptoms**: `docker compose up` fails or containers exit immediately

**Solutions**:
1. Check logs: `docker compose logs`
2. Verify port availability:
   ```bash
   lsof -i :11434  # Ollama
   lsof -i :8642   # Main agent
   lsof -i :8888   # SearXNG
   ```
3. Ensure `.env` exists: `cp .env.template .env`
4. Rebuild image: `docker build --no-cache -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .`

#### Out of Memory

**Symptoms**: Container crashes, slow performance, OOM kills

**Solutions**:
1. Check available memory: `docker stats`
2. Reduce agent count — comment out `researcher` and `simple-agent` in `compose.yaml`
3. Reduce memory limits in `compose.yaml`:
   ```yaml
   agent-main:
     deploy:
       resources:
         limits:
           memory: 2G
   ```
4. Close unnecessary applications

### Ollama Issues

#### Ollama Container Unhealthy

**Symptoms**: Health check fails, agents can't connect to model provider

**Solutions**:
1. Check Ollama logs: `docker compose logs ollama`
2. Verify API endpoint:
   ```bash
   curl http://localhost:11434/api/tags
   ```
3. Pull a model:
   ```bash
   docker exec bob-the-agent-ollama ollama pull qwen3.5:2b-q4_K_M
   ```
4. Sign into Ollama for cloud models:
   ```bash
   docker exec -it bob-the-agent-ollama ollama signin
   ```

#### Model Not Found

**Symptoms**: "model not found" error in agent logs

**Solutions**:
1. List available models:
   ```bash
   docker exec bob-the-agent-ollama ollama list
   ```
2. Pull the model referenced in the agent's `hermes.partial.yml`:
   ```bash
   docker exec bob-the-agent-ollama ollama pull kimi-k2.6:cloud
   ```
3. Check model name spelling matches the partial config

#### Slow Inference

**Symptoms**: Responses take too long

**Solutions**:
1. Use cloud models for better performance (e.g., `kimi-k2.6:cloud`)
2. Use smaller local models for simple tasks (e.g., `qwen3.5:2b-q4_K_M`)
3. Enable GPU if available
4. Increase `agent.max_turns` or `agent.gateway_timeout` in hermes.template.yaml

### Agent Issues

#### Image Not Found

**Symptoms**: `docker compose up` fails with "image bob-the-agent:latest not found"

**Solutions**:
1. Build the image first:
   ```bash
   docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .
   ```
2. Or use the helper script: `./run-docker.sh`
3. Or pull from a registry (if configured):
   ```bash
   docker pull your-registry/bob-the-agent:latest
   docker tag your-registry/bob-the-agent:latest bob-the-agent:latest
   ```

#### Agent Won't Start

**Symptoms**: Agent container exits or restarts

**Solutions**:
1. Check logs:
   ```bash
   docker compose logs agent-main
   ```
2. Verify Hermes config was generated — check logs for "Config not found! Generating..."
3. Check Ollama connection:
   ```bash
   docker exec bob-the-agent ping ollama
   ```
4. Verify gateway port is available (8642 for main, 8101 for researcher, 8102 for simple, 8103 for coder)
5. Check volume mounts exist:
   ```bash
   ls -la volumes/agent-main/
   ```

#### Config Generation Errors

**Symptoms**: Agent fails to start, config merge errors

**Solutions**:
1. Check the agent's partial YAML for syntax errors
2. Verify `hermes.template.yaml` exists at `/app/config/hermes.template.yaml`
3. Check the merge script:
   ```bash
   docker exec bob-the-agent cat /opt/data/config.yaml
   ```
4. Remove generated config to force regeneration:
   ```bash
   docker compose stop agent-main
   rm volumes/agent-main/config.yaml
   docker compose start agent-main
   ```

#### Specific Agent Issues

For issues with a specific agent container, check its logs:

```bash
# Main agent
docker compose logs agent-main

# Researcher
docker compose logs researcher

# Simple agent
docker compose logs simple-agent

# Coder agent
docker compose logs coder
```

#### Coder Agent Issues

##### OpenCode CLI Not Installed

**Symptoms**: Coder agent logs show "opencode: command not found" or coding tasks fail

**Solutions**:
1. Check coder logs for installation errors:
   ```bash
   docker compose logs coder | grep -i opencode
   ```
2. Manually install OpenCode CLI in the container:
   ```bash
   docker exec bob-the-agent-coder npm install -g opencode-ai@latest
   ```
3. Verify installation:
   ```bash
   docker exec bob-the-agent-coder opencode --version
   ```
4. Restart the coder container to retry auto-installation:
   ```bash
   docker compose restart coder
   ```

##### OpenCode Config Not Generated

**Symptoms**: OpenCode uses default configuration instead of project-specific settings

**Solutions**:
1. Check if the config file exists:
   ```bash
   docker exec bob-the-agent-coder cat ~/.config/opencode/opencode.json
   ```
2. Check if the template exists:
   ```bash
   docker exec bob-the-agent-coder cat /app/config/opencode.template.jsonc
   ```
3. Remove existing config and restart to force regeneration:
   ```bash
   docker exec bob-the-agent-coder rm -f ~/.config/opencode/opencode.json
   docker compose restart coder
   ```

##### Coder Agent Out of Memory

**Symptoms**: Coder container crashes or is OOM-killed during large coding tasks

**Solutions**:
1. The coder agent has higher memory limits (8G) than other agents (4G). If this is still insufficient:
   ```yaml
   coder:
     deploy:
       resources:
         limits:
           memory: 12G
   ```
2. Check current memory usage:
   ```bash
   docker stats bob-the-agent-coder
   ```
3. For resource-constrained systems, disable the coder agent and delegate coding tasks to the main agent instead.

##### Projects Workspace Issues

**Symptoms**: OpenCode cannot find or access project files

**Solutions**:
1. Verify the projects directory exists and is mounted:
   ```bash
   docker exec bob-the-agent-coder ls -la /app/projects
   ```
2. Check host-side directory:
   ```bash
   ls -la ./volumes/projects/
   ```
3. Ensure the directory has proper permissions:
   ```bash
   docker exec bob-the-agent-coder chmod -R 755 /app/projects
   ```

### SearXNG Issues

#### Search Not Working

**Symptoms**: Web search returns errors or no results

**Solutions**:
1. Check if SearXNG is running:
   ```bash
   docker compose ps searxng
   ```
2. Check SearXNG logs:
   ```bash
   docker compose logs searxng
   ```
3. Verify SearXNG API:
   ```bash
   curl "http://localhost:8888/search?q=test&format=json"
   ```
4. Check Valkey (Redis) is healthy:
   ```bash
   docker compose ps valkey
   docker exec bob-the-agent-valkey valkey-cli ping
   ```

### Discord Bot Issues

#### Bot Offline

**Symptoms**: Bot shows as offline in Discord

**Solutions**:
1. Verify `DISCORD_BOT_TOKEN` in `.env`
2. Check agent-main logs for Discord errors:
   ```bash
   docker compose logs agent-main | grep -i discord
   ```
3. Restart the main agent: `docker compose restart agent-main`

#### Commands Not Working

**Symptoms**: Bot doesn't respond to commands

**Solutions**:
1. Check that Discord toolset is enabled in the agent's config
2. Re-invite bot with correct permissions
3. Verify `DISCORD_CLIENT_ID` in `.env`
4. Check Discord Developer Portal for errors

### Network Issues

#### Services Can't Communicate

**Symptoms**: Connection refused between containers

**Solutions**:
1. Check Docker network:
   ```bash
   docker network ls
   docker network inspect bob-the-agent-network
   ```
2. Verify service names match in config
3. Recreate network:
   ```bash
   docker compose down
   docker compose up -d
   ```

#### External Access Issues

**Symptoms**: Can't access from external network

**Solutions**:
1. Check firewall rules
2. Verify port binding is correct
3. Use reverse proxy for production

## Diagnostic Commands

```bash
# View all logs
docker compose logs

# Follow specific service
docker compose logs -f agent-main
docker compose logs -f researcher
docker compose logs -f simple-agent
docker compose logs -f coder

# Check container status
docker compose ps

# Inspect container
docker inspect bob-the-agent

# Check resource usage
docker stats

# Enter container shell
docker exec -it bob-the-agent bash
docker exec -it bob-the-agent-coder bash

# Test main agent health
curl http://localhost:8642/healthz

# Check Ollama
docker exec bob-the-agent-ollama ollama list

# Test SearXNG
curl "http://localhost:8888/search?q=test&format=json"

# View generated config
docker exec bob-the-agent cat /opt/data/config.yaml

# View agent SOUL.md
docker exec bob-the-agent cat /opt/data/SOUL.md

# Verify OpenCode is installed in coder container
docker exec bob-the-agent-coder opencode --version

# Check coder agent volume
ls -la volumes/agent-coder/

# Check projects workspace
ls -la volumes/projects/
```

## Getting Help

1. Check this troubleshooting guide
2. Review [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs)
3. Check [Ollama docs](https://ollama.com/docs)
4. Open an issue with:
   - Error logs
   - `docker compose config` output
   - Steps to reproduce