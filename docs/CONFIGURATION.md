# Configuration Guide

## Overview

Bob The Agent uses environment variables and YAML configuration files for customization. The system is built on the Hermes Agent framework with a single-container architecture: one `agent` container runs the Hermes gateway with the default profile (`main`) plus the multiplexed `researcher`, `simple`, and `coder` profiles, each with its own configuration.

## Configuration Files

### .env

Main configuration file for environment variables.

[.env.template](/.env.template) contains possible environment variables with explanations.

### hermes.template.yaml

Shared Hermes Agent behavior configuration template located at `src/config/hermes.template.yaml`.

**How it works** (at container startup, in `bootstrap.sh`):
1. For the default profile (`main`, at `/opt/data/config.yaml`) and each secondary profile (`/opt/data/profiles/{name}/config.yaml`):
   - If the config does not exist, `generate-config.sh` reads the YAML template and the profile's partial
   - If a partial exists for the profile, `merge-yaml.mjs` deep-merges them (arrays replaced, objects merged, partial wins)
   - If no partial exists, the template is used as-is
   - If the config already exists (e.g. from a previous boot or a migrated install), it is **reconciled** with the current partial instead — deep-merged again (partial wins, profile-only keys preserved) after a one-time `config.yaml.pre-reconcile.bak` snapshot
2. Final config is written to the profile's `config.yaml`

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
  gateway_timeout: 2700
  personalities:
    default: You are automation agent...
terminal:
  backend: local
  persistent_shell: true
  lifetime_seconds: 300
approvals:
  mode: off
```

### Profile Partials (hermes.partial.yml)

Each profile overrides specific parts of the shared template via a partial YAML file in `src/agents/{name}/hermes.partial.yml`:

**Main profile** (`src/agents/main/hermes.partial.yml`):
```yaml
model:
  default: qwen3.5:2b-q4_K_M
  provider: custom
  base_url: http://ollama:11434/v1
  # Cap the per-request ollama context (Hermes otherwise auto-detects the
  # GGUF's advertised 256k and sends num_ctx=262144 per request).
  ollama_num_ctx: 65536
custom_providers:
  - name: ollama/qwen3.5:2b-q4_K_M
    base_url: http://ollama:11434/v1
    model: qwen3.5:2b-q4_K_M
toolsets:
  - hermes-cli
  - browser
# Multiplex the researcher/simple/coder profiles behind this gateway.
gateway:
  multiplex_profiles: true
agent:
  reasoning_effort: false      # disable model thinking on CPU-only hosts
  bot_mode_protocol: true
  local_stream_stale_timeout: 3600
```

**Researcher / simple profiles** (`src/agents/researcher|simple/hermes.partial.yml`): same model wiring and runtime knobs as main (without `gateway.multiplex_profiles`, which only the default profile sets).

**Coder profile** (`src/agents/coder/hermes.partial.yml`):
```yaml
model:
  default: qwen3.5:2b-q4_K_M
  provider: custom
  base_url: http://ollama:11434/v1
  ollama_num_ctx: 65536
custom_providers:
  - name: ollama/qwen3.5:2b-q4_K_M
    base_url: http://ollama:11434/v1
    model: qwen3.5:2b-q4_K_M
toolsets:
  - hermes-cli
  - browser
agent:
  max_turns: 120
  gateway_timeout: 7200
  reasoning_effort: false
  local_stream_stale_timeout: 3600
terminal:
  backend: local
  cwd: /app/projects
  timeout: 3600   # OpenCode runs take minutes per LLM call on CPU-only hosts
```

The coder profile uses higher limits (120 max_turns, 7200s gateway timeout, 3600s terminal timeout) than other profiles because coding tasks typically take longer to complete.

### Runtime knobs worth knowing

| Key | Default here | Purpose |
|-----|--------------|---------|
| `model.ollama_num_ctx` | `65536` | Per-request ollama context window. Hermes otherwise auto-detects the model's advertised context (e.g. 262144 for qwen3.5) and sends it per request; 65536 is the minimum Hermes considers reliable for its tool-use prompts. Must stay <= `OLLAMA_CONTEXT_LENGTH` on the ollama container. |
| `agent.reasoning_effort` | `false` | Sends `reasoning_effort "none"` + `think=false` so local models do not spend ~9 minutes thinking per trivial reply on CPU-only hosts. |
| `agent.local_stream_stale_timeout` | `3600` | Hermes kills model connections that receive no chunks for 900s by default; on slow CPU inference a long prefill looks exactly like a stalled stream. Raised to 3600s so big-prompt turns survive prefill. |
| `approvals.mode` | `off` | Full approval bypass across interactive, gateway, cron and unattended contexts — required for 24/7 unattended operation. Replaces the old auto-approve env var, denylisted upstream in Hermes v0.21. Valid modes: `manual | smart | off`. |
| `gateway.multiplex_profiles` | `true` (main only) | Makes the single gateway serve all profiles. |

**Merge behavior:**
- Objects are deep-merged (nested keys override individually)
- Arrays are replaced entirely (not concatenated)
- Keys present only in the partial are added
- Keys present only in the template (or in an existing config) are preserved

### Per-profile .env

Bootstrap writes a `.env` (chmod 600) into each secondary profile directory (`/opt/data/profiles/{name}/.env`) carrying the profile's secret scope (Ollama/SearXNG URLs, x-com/grok/AWS S3 keys). Hermes never inherits the root `.env` into profiles.

### Agent Identity Files

Each profile also has identity and behavioral files that are copied to its workspace at startup:

| File | Purpose |
|------|---------|
| `src/agents/{name}/SOUL.md` | Agent personality and behavioral instructions |
| `src/agents/{name}/IDENTITY.md` | Agent identity metadata (name, role, emoji) |
| `src/agents/{name}/AGENTS.md` | Agent workspace instructions and conventions |
| `src/agents/{name}/TOOLS.md` | Agent-specific tool notes and tips |

### OpenCode Configuration (Coder Profile Only)

The coder profile uses OpenCode CLI as its coding engine, configured via a template that is environment-substituted at container startup. OpenCode CLI itself is installed at image build time (`opencode-ai@latest`, unpinned — rebuilds may pick up new versions).

**Template file:** `src/config/opencode.template.jsonc`

At startup, `bootstrap.sh` generates the OpenCode config by replacing placeholders in the template:

| Placeholder | Replaced With |
|-------------|---------------|
| `OLLAMA_BASE_URL_PLACEHOLDER` | Value of `OLLAMA_BASE_URL` env var |
| `SEARXNG_BASE_URL_PLACEHOLDER` | Value of `SEARXNG_BASE_URL` env var |

**Generated config location:** `/opt/data/.config/opencode/opencode.jsonc` (pinned by the `OPENCODE_CONFIG` compose env var)

The OpenCode config defines:
- **Provider**: Ollama with the coder profile's model
- **Agents**: coder (16K token limit), task (8K token limit), title
- **Tools**: Full tool profile with SearXNG web search
- **Shell**: `/bin/bash -l`
- **Auto-compact**: Enabled for long coding sessions

## Environment Variables

### Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | string | `info` | Logging level (debug, info, warn, error) |
| `NODE_ENV` | string | `production` | Node environment |
| `OLLAMA_CONTEXT_LENGTH` | number | `65536` | Ollama server-side context length. Must stay >= the `ollama_num_ctx` (65536) pinned in the profile partials — without it the ollama container defaults to 4096 and truncates real agent prompts. |
| `HERMES_API_TIMEOUT` | number | `3600` | Hermes API read timeout in seconds. Raised for CPU-only hosts where a fresh ~16k-token prompt prefills at ~20 tok/s (~13-15 min), beyond Hermes' 900s default. |
| `HERMES_UID` / `HERMES_GID` | number | - | Linux hosts: run the container with these UID/GID so the `./volumes/*` bind mounts are owned by your user |

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

The compose file defines 4 services. All profiles live in the single `agent` service (image `bob-the-agent:latest`); bootstrap provisions the profiles and execs the gateway:

```yaml
services:
  ollama:          # LLM inference engine
  agent:           # Single Hermes Agent container (profiles: main, researcher, simple, coder)
  searxng:         # Privacy-respecting metasearch engine
  valkey:          # Redis-compatible cache for SearXNG
```

### Resource Limits

The `agent` service reserves 2 CPUs / 4G memory with 8G memory limits:

```yaml
agent:
  deploy:
    resources:
      reservations:
        cpus: 2
        memory: 4G
      limits:
        cpus: 2
        memory: 8G
```

### Volume Mounts

| Host Path | Container Path | Service | Purpose |
|-----------|---------------|---------|---------|
| `./volumes/agent` | `/opt/data` | agent | Single agent workspace: main profile at the root, researcher/simple/coder under `profiles/` |
| `./volumes/results` | `/app/results` | agent | Final task output files |
| `./volumes/projects` | `/app/projects` | agent | Shared project workspace (git repos, code) |
| `ollama_data` | `/root/.ollama` | ollama | Downloaded models |
| `searxng_config` | `/etc/searxng/` | searxng | SearXNG configuration |
| `searxng_data` | `/var/cache/searxng/` | searxng | SearXNG cache |
| `valkey_data` | `/data` | valkey | Valkey/Redis data |

### Ports

| Host Port | Container Port | Service |
|-----------|---------------|---------|
| `127.0.0.1:8642` | `8642` | agent Gateway (loopback-only; use an SSH tunnel for remote access: `ssh -L 8642:localhost:8642 <host>`) |
| `11434` | `11434` | Ollama API |
| `8888` | `8888` | SearXNG |

## Model Configuration

### Changing Profile Models

Each profile's model is configured via its `hermes.partial.yml` file:

1. Edit the partial for the profile you want to change, e.g. `src/agents/main/hermes.partial.yml`:

```yaml
model:
  default: your-model-name:cloud
  provider: custom
  base_url: http://ollama:11434/v1
  ollama_num_ctx: 65536
custom_providers:
  - name: ollama/your-model-name:cloud
    base_url: http://ollama:11434/v1
    model: your-model-name:cloud
```

2. Restart the container. Existing profile configs are reconciled with the updated partial at boot (partial wins; a one-time `.pre-reconcile.bak` snapshot of the pre-reconcile config is taken):
```bash
docker compose up -d --force-recreate agent
```

### Adding Models to Ollama

1. Pull additional models:
   ```bash
   docker exec bob-the-agent-ollama ollama pull your-model
   ```

2. Reference them in profile partials via `custom_providers`.

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

3. Reference in profile partials as shown above. Note the local ollama container cannot serve `:cloud` models until it is signed in — until then profiles resolve the local default model.

## Security Considerations

### Password Security

- Use strong passwords (16+ characters)
- Change default credentials immediately

### Network Security

- The agent gateway port (8642) is bound to `127.0.0.1` only — it fronts all profiles behind `approvals.mode: off` and its auth is unverified, so do not expose it. Use an SSH tunnel (`ssh -L 8642:localhost:8642 <host>`) for remote access
- Use Docker network isolation (services communicate on the internal `bob-the-agent-network`)

### API Keys

- Never commit API keys to git
- Use environment variables (`.env` file)
- Rotate keys periodically

## Performance Tuning

### Memory Allocation

For limited RAM, reduce the agent container's limits:

```yaml
agent:
  deploy:
    resources:
      limits:
        memory: 4G
```

Profiles are multiplexed in the one container, so there is no per-agent container to disable — reducing limits affects all profiles.

### Model Selection

- Use cloud models (e.g., `kimi-k2.6:cloud`) for best quality (requires `ollama signin` in the ollama container)
- Use local models (e.g., the default `qwen3.5:2b-q4_K_M`) for offline operation or CPU-only hosts
- All profiles currently pin the local default model, overridden per profile via each partial

### Profile Count

All four profiles run inside the single container. To reduce load, set `gateway.multiplex_profiles: false` in the main partial so only the `main` profile is served (or remove the profile partials entirely — profiles are only provisioned from `src/agents/`).
