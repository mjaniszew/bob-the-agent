#!/bin/bash
set -e

echo "Agent starting"

# Config generation parameters with defaults
TEMPLATE_FILE="${TEMPLATE_FILE:-/app/config/hermes.template.yaml}"
AGENTS_DIR="${AGENTS_DIR:-/app/agents}"
AGENT_NAME="${AGENT_NAME:-main}"
OUTPUT_FILE="${OUTPUT_FILE:-/opt/data/config.yaml}"

# Generate config if does not exist
if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "Config not found! Generating..."
  /app/scripts/generate-config.sh "$TEMPLATE_FILE" "$AGENTS_DIR" "$AGENT_NAME" "$OUTPUT_FILE"
fi

if [[ ! -f "/opt/data/SOUL.md" ]]; then
  echo "Agent SOUL not found! Generating..."
  cp $AGENTS_DIR/$AGENT_NAME/SOUL.md /opt/data/SOUL.md
fi

# Set up skills directory for agent
if [[ ! -d /opt/data/skills ]]; then
  mkdir -p /opt/data/skills
fi

# Synchronize skill definitions to hermes skills directory if not already there
SKIP_DIRS=("dir" "node_modules")

for skill_dir in /app/skills/*/; do
  if [[ -d "$skill_dir" ]]; then
    skill_name=$(basename "$skill_dir")

    # Skip blacklisted directories
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

# Execute the main command
exec "$@"