#!/bin/bash
set -e

echo "Coder Agent starting"

# ===========================================
# Step 1: Generate Hermes config if needed
# ===========================================
TEMPLATE_FILE="${TEMPLATE_FILE:-/app/config/hermes.template.yaml}"
AGENTS_DIR="${AGENTS_DIR:-/app/agents}"
AGENT_NAME="${AGENT_NAME:-coder}"
OUTPUT_FILE="${OUTPUT_FILE:-/opt/data/config.yaml}"

if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "Config not found! Generating..."
  /app/scripts/generate-config.sh "$TEMPLATE_FILE" "$AGENTS_DIR" "$AGENT_NAME" "$OUTPUT_FILE"
fi

if [[ ! -f "/opt/data/SOUL.md" ]]; then
  echo "Agent SOUL not found! Generating..."
  cp "$AGENTS_DIR/$AGENT_NAME/SOUL.md" /opt/data/SOUL.md
fi

# Set up skills directory
if [[ ! -d /opt/data/skills ]]; then
  mkdir -p /opt/data/skills
fi

# Synchronize skill definitions to hermes skills directory if not already there
SKIP_DIRS=("dir" "node_modules")
for skill_dir in /app/skills/*/; do
  if [[ -d "$skill_dir" ]]; then
    skill_name=$(basename "$skill_dir")
    skip=false
    for skip_dir in "${SKIP_DIRS[@]}"; do
      if [[ "$skill_name" == "$skip_dir" ]]; then
        skip=true
        break
      fi
    done
    $skip && continue
    # Copy entire folder structure
    cp -r "$skill_dir" "/opt/data/skills"
  fi
done

# ===========================================
# Step 2: Install OpenCode CLI if not present
# ===========================================
if ! command -v opencode &> /dev/null; then
  echo "OpenCode CLI not found, installing..."
  npm install -g opencode-ai@latest 2>&1 || {
    echo "Warning: npm install failed, trying curl install..."
    curl -fsSL https://opencode.ai/install | bash
  }
fi

echo "OpenCode CLI version: $(opencode --version 2>/dev/null || echo 'not available')"

# ===========================================
# Step 3: Generate OpenCode config from template
# ===========================================
OPENCODE_CONFIG_DIR="~/.config/opencode"
OPENCODE_CONFIG_FILE="${OPENCODE_CONFIG_DIR}/opencode.json"
OPENCODE_TEMPLATE="/app/config/opencode.template.json"

if [[ ! -f "$OPENCODE_CONFIG_FILE" ]]; then
  echo "OpenCode config not found, generating from template..."

  mkdir -p "$OPENCODE_CONFIG_DIR"

  OLLAMA_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
  SEARXNG_URL="${SEARXNG_BASE_URL:-http://searxng:8888}"

  if [[ -f "$OPENCODE_TEMPLATE" ]]; then
    sed \
      -e "s|OLLAMA_BASE_URL_PLACEHOLDER|${OLLAMA_URL}|g" \
      -e "s|SEARXNG_BASE_URL_PLACEHOLDER|${SEARXNG_URL}|g" \
      "$OPENCODE_TEMPLATE" > "$OPENCODE_CONFIG_FILE"
    echo "OpenCode config generated at $OPENCODE_CONFIG_FILE"
  else
    echo "Warning: OpenCode template not found at $OPENCODE_TEMPLATE"
    echo "OpenCode will use default configuration."
  fi
else
  echo "OpenCode config already exists, skipping generation."
fi

# ===========================================
# Step 4: Prepare projects workspace directory
# ===========================================
mkdir -p /app/projects
echo "Projects workspace ready at /app/projects"

# ===========================================
# Step 5: Start NATS listener for inter-agent communication
# ===========================================
if [[ -n "${AGENT_NAME}" && -f "/app/scripts/register-nats.py" ]]; then
  mkdir -p /opt/data/nats-messages/incoming
  python3 /app/scripts/register-nats.py &
  NATS_PID=$!
  echo "NATS listener started for agent: ${AGENT_NAME} (PID: ${NATS_PID})"
fi

# ===========================================
# Step 6: Execute the main command (Hermes gateway)
# ===========================================
echo "Coder Agent setup complete, starting Hermes gateway..."
exec "$@"