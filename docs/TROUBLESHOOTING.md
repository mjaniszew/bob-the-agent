# Troubleshooting Guide

## Common Issues

### Container Issues

#### Containers Won't Start

**Symptoms**: `docker compose up` fails or containers exit immediately

**Solutions**:
1. Check logs: `docker compose logs`
2. Verify port availability:
   ```bash
   lsof -i :11434
   lsof -i :18789
   ```
3. Ensure `.env` exists: `cp .env.template .env`
4. Rebuild containers: `docker compose build --no-cache`

#### Out of Memory

**Symptoms**: Container crashes, slow performance

**Solutions**:
1. Check available memory: `docker stats`
2. Reduce model size: use `llama3.2:1b`
3. Adjust memory limits in `docker-compose.yml`:
   ```yaml
   ollama:
     deploy:
       resources:
         limits:
           memory: 4G
   ```
4. Close unnecessary applications

### Ollama Issues

#### Ollama Container Unhealthy

**Symptoms**: Health check fails, agent can't connect

**Solutions**:
1. Check Ollama logs: `docker compose logs ollama`
2. Verify API endpoint:
   ```bash
   curl http://localhost:11434/api/tags
   ```
3. Pull a model:
   ```bash
   docker exec bob-the-agent-ollama ollama pull llama3.2
   ```

#### Model Not Found

**Symptoms**: "model not found" error

**Solutions**:
1. List available models:
   ```bash
   docker exec bob-the-agent-ollama ollama list
   ```
2. Pull the model:
   ```bash
   docker exec bob-the-agent-ollama ollama pull llama3.2
   ```
3. Check model name spelling

#### Slow Inference

**Symptoms**: Responses take too long

**Solutions**:
1. Use smaller model: `llama3.2:1b`
2. Enable GPU if available
3. Reduce context length in config

### Agent Issues

#### Agent Won't Start

**Symptoms**: Agent container exits or restarts

**Solutions**:
1. Check configuration:
   ```bash
   docker compose logs agent
   ```
2. Verify OpenClaw config was generated: check logs for "First run: openclaw.json generated"
3. Check Ollama connection:
   ```bash
   docker exec bob-the-agent ping ollama
   ```
4. Verify gateway port is available

#### Database Errors

**Symptoms**: SQLite errors, tasks not persisting

**Solutions**:
1. Check volume mounts:
   ```bash
   ls -la volumes/data/
   ```
2. Fix permissions:
   ```bash
   chmod -R 755 volumes/
   ```
3. Remove and recreate database:
   ```bash
   rm volumes/data/tasks.db
   docker compose restart agent
   ```

### Discord Bot Issues

#### Bot Offline

**Symptoms**: Bot shows as offline in Discord

**Solutions**:
1. Verify `DISCORD_BOT_TOKEN` in `.env`
2. Check agent logs for Discord errors
3. Restart agent: `docker compose restart agent`

#### Commands Not Working

**Symptoms**: Slash commands don't appear or respond

**Solutions**:
1. Re-invite bot with correct permissions
2. Verify `DISCORD_CLIENT_ID` in `.env`
3. Check Discord Developer Portal for errors

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
docker compose logs -f agent

# Check container status
docker compose ps

# Inspect container
docker inspect bob-the-agent

# Check resource usage
docker stats

# Enter container shell
docker exec -it bob-the-agent bash

# Test API endpoints
curl http://localhost:18789/healthz

# Check Ollama
docker exec bob-the-agent-ollama ollama list
```

## Getting Help

1. Check this troubleshooting guide
2. Review [OpenClaw docs](https://docs.openclaw.ai)
3. Check [Ollama docs](https://ollama.com/docs)
4. Open an issue with:
   - Error logs
   - `docker compose config` output
   - Steps to reproduce