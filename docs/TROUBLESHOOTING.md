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
2. Reduce memory limits in `compose.yaml` (all profiles share the single `agent` container):
   ```yaml
   agent:
     deploy:
       resources:
         limits:
           memory: 4G
   ```
3. Close unnecessary applications

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
4. Increase `agent.max_turns` or `agent.gateway_timeout` in the profile's `hermes.partial.yml`

#### Stream Stale — Connection Killed Mid-Prefill

**Symptoms**: Log shows `Stream stale for Ns — no chunks received. Killing connection.` and long turns abort

**Cause**: Hermes' local stale-stream detector kills model connections that receive no chunks for 900s by default. On a CPU-only host, a fresh large prompt prefills slowly (e.g. ~20 tok/s for a ~16k-token prompt ≈ 13-15 minutes) and looks exactly like a stalled stream.

**Solutions**:
1. Raise the stale timeout in the affected profile's partial (`agent.local_stream_stale_timeout: 3600` is already pinned for CPU-only hosts)
2. Or raise `HERMES_API_TIMEOUT` in `.env` if the read timeout (not the stale detector) is what fires

#### Ollama Runtime Context Too Small

**Symptoms**: Error like "Ollama runtime context is too small for Hermes tool use", or garbled/truncated responses on large prompts

**Cause**: The ollama container defaults to a 4096-token context; Hermes' agent prompts (~16k tokens) are truncated to destruction. Hermes' minimum for reliable tool use is 65536.

**Solutions**:
1. Set `OLLAMA_CONTEXT_LENGTH=65536` (compose default) for the ollama container — it must stay >= the `model.ollama_num_ctx` (65536) pinned in `src/agents/*/hermes.partial.yml`
2. Restart the stack: `docker compose up -d --force-recreate ollama agent`

#### First Boot Takes Very Long

**Symptoms**: `agent` container stays "starting" (health) beyond the 120s `start_period`

**Cause**: On a first boot with an empty ollama volume, bootstrap auto-pulls the default local model (~1.6GB download, capped at 30 minutes) before the gateway starts listening.

**Solutions**:
1. This is expected on the very first boot — watch progress with `docker compose logs -f agent`
2. Later boots skip the pull and start normally

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
   docker compose logs agent
   ```
2. Verify Hermes config was generated — check logs for "generating default (main) profile config"
3. Check Ollama connection:
   ```bash
   docker exec bob-the-agent ping ollama
   ```
4. Verify gateway port is available (8642, loopback-only binding)
5. Check volume mounts exist:
   ```bash
   ls -la volumes/agent/
   ```

#### Profile Lock — Two Agent Processes on One Profile

**Symptoms**: A profile fails to start with a lock error (e.g. "profile lock", "another instance is running"), or `hermes -p <profile>` exits immediately

**Cause**: Hermes takes a lock per profile directory. A second agent process targeting the same profile as the multiplexed gateway (or an orphaned lock after an unclean container kill) cannot start.

**Solutions**:
1. Never run `hermes -p main ...` inside the container — the gateway already owns the `main` profile; delegate to the secondary profiles (`researcher`, `simple`, `coder`) instead
2. If the lock is orphaned (e.g. after a crash), remove the stale lock file and restart:
   ```bash
   docker exec bob-the-agent rm -f /opt/data/profiles/<name>/gateway.lock
   docker compose restart agent
   ```

#### Gateway Session Cannot Reach Other Profiles

**Symptoms**: `message_agent` from a gateway (multiplexed) session fails to deliver to another profile, or no response arrives

**Cause**: Upstream Hermes limitation — a multiplexed gateway session cannot message other profiles (tracked in Hermes issue #91260). The per-profile Bot Chat sessions created at bootstrap are the future messaging path.

**Solutions**:
1. Use the `delegate-profile` skill instead — it runs `hermes -p <profile> chat --oneshot` in-process, which works today (foreground or background with `check_task` polling)

#### Config Generation Errors

**Symptoms**: Agent fails to start, config merge errors

**Solutions**:
1. Check the profile's partial YAML for syntax errors
2. Verify `hermes.template.yaml` exists at `/app/config/hermes.template.yaml`
3. Check the merged config:
   ```bash
   docker exec bob-the-agent cat /opt/data/config.yaml
   ```
4. Remove the generated config to force regeneration:
   ```bash
   docker compose stop agent
   rm volumes/agent/config.yaml
   docker compose start agent
   ```
5. The one-time `config.yaml.pre-reconcile.bak` snapshot (taken before the first reconcile of a migrated config) can be restored if a reconcile went wrong

#### Specific Profile Issues

All profiles run in the single `agent` container. For profile-specific errors, check the container logs and the profile's own logs:

```bash
# Container (all profiles)
docker compose logs agent

# Per-profile session/state under the volume
ls -la volumes/agent/profiles/<name>/
```

#### Coder Profile Issues

##### OpenCode CLI Not Installed

**Symptoms**: Coder profile tasks fail with "opencode: command not found"

**Solutions**:
1. OpenCode CLI is installed at image build (`npm install -g opencode-ai@latest`). Rebuild the image if it is missing:
   ```bash
   docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .
   docker compose up -d --force-recreate agent
   ```
2. Manually install OpenCode CLI in the container:
   ```bash
   docker exec bob-the-agent npm install -g opencode-ai@latest
   ```
3. Verify installation:
   ```bash
   docker exec bob-the-agent opencode --version
   ```

##### OpenCode Config Not Generated

**Symptoms**: OpenCode uses default configuration instead of project-specific settings

**Solutions**:
1. Check if the config file exists:
   ```bash
   docker exec bob-the-agent cat /opt/data/.config/opencode/opencode.jsonc
   ```
2. Check if the template exists:
   ```bash
   docker exec bob-the-agent cat /app/config/opencode.template.jsonc
   ```
3. Remove existing config and restart to force regeneration:
   ```bash
   docker exec bob-the-agent rm -f /opt/data/.config/opencode/opencode.jsonc
   docker compose restart agent
   ```

##### Coder Profile Out of Memory

**Symptoms**: Container crashes or is OOM-killed during large coding tasks (Hermes + OpenCode CLI in the same container)

**Solutions**:
1. The agent container has 8G limits. If this is still insufficient:
   ```yaml
   agent:
     deploy:
       resources:
         limits:
           memory: 12G
   ```
2. Check current memory usage:
   ```bash
   docker stats bob-the-agent
   ```
3. For resource-constrained systems, delegate fewer coding tasks or use a smaller model for the coder profile.

##### Projects Workspace Issues

**Symptoms**: OpenCode cannot find or access project files

**Solutions**:
1. Verify the projects directory exists and is mounted:
   ```bash
   docker exec bob-the-agent ls -la /app/projects
   ```
2. Check host-side directory:
   ```bash
   ls -la ./volumes/projects/
   ```
3. Ensure the directory has proper permissions:
   ```bash
   docker exec bob-the-agent chmod -R 755 /app/projects
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
2. Check the agent container's logs for Discord errors:
   ```bash
   docker compose logs agent | grep -i discord
   ```
3. Restart the agent: `docker compose restart agent`

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

# Follow the agent container (all profiles)
docker compose logs -f agent

# Check container status
docker compose ps

# Inspect container
docker inspect bob-the-agent

# Check resource usage
docker stats

# Enter container shell
docker exec -it bob-the-agent bash

# Test main agent health
curl http://localhost:8642/healthz

# Check Ollama
docker exec bob-the-agent-ollama ollama list

# Test SearXNG
curl "http://localhost:8888/search?q=test&format=json"

# View generated config (main profile; secondary profiles under volumes/agent/profiles/<name>/)
docker exec bob-the-agent cat /opt/data/config.yaml

# View agent SOUL.md
docker exec bob-the-agent cat /opt/data/SOUL.md

# Verify OpenCode is installed
docker exec bob-the-agent opencode --version

# Check agent volume (profiles under profiles/)
ls -la volumes/agent/
ls -la volumes/agent/profiles/

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
