# Troubleshooting Guide

## Common Issues

### Container Issues

#### Containers Won't Start

**Symptoms**: `docker compose up` fails or containers exit immediately

**Solutions**:
1. Check logs: `docker compose logs`
2. Verify port availability:
   ```bash
   lsof -i :8080
   lsof -i :11434
   lsof -i :18789
   ```
3. Ensure `.env` exists: `cp .env.example .env`
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
   docker exec mini-agent-ollama ollama pull llama3.2
   ```

#### Model Not Found

**Symptoms**: "model not found" error

**Solutions**:
1. List available models:
   ```bash
   docker exec mini-agent-ollama ollama list
   ```
2. Pull the model:
   ```bash
   docker exec mini-agent-ollama ollama pull llama3.2
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
2. Verify OpenClaw config: `src/config/openclaw.yaml`
3. Check Ollama connection:
   ```bash
   docker exec mini-agent ping ollama
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

### Web Interface Issues

#### Can't Access Dashboard

**Symptoms**: Connection refused, timeout

**Solutions**:
1. Check web container: `docker compose ps web`
2. Verify port 8080 is free
3. Check nginx logs: `docker compose logs web`
4. Try alternative port in `docker-compose.yml`

#### Login Fails

**Symptoms**: "Invalid credentials" error

**Solutions**:
1. Verify credentials in `.env`
2. Clear browser cookies
3. Restart container: `docker compose restart web`

#### API Errors

**Symptoms**: 500 errors, CORS issues

**Solutions**:
1. Check API logs: `docker compose logs agent`
2. Verify `ALLOWED_ORIGINS` in `.env`
3. Check JWT token expiration

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
   docker network inspect mini-agent-network
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
2. Verify port binding: `0.0.0.0:8080:80` not `127.0.0.1:8080:80`
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
docker inspect mini-agent

# Check resource usage
docker stats

# Enter container shell
docker exec -it mini-agent bash

# Test API endpoints
curl http://localhost:18789/healthz
curl http://localhost:3001/api/status

# Check Ollama
docker exec mini-agent-ollama ollama list
```

## Getting Help

1. Check this troubleshooting guide
2. Review [OpenClaw docs](https://docs.openclaw.ai)
3. Check [Ollama docs](https://ollama.com/docs)
4. Open an issue with:
   - Error logs
   - `docker compose config` output
   - Steps to reproduce