#!/bin/bash
set -e

echo "Agent starting"

# Generate config if does not exist
if [[ ! -f /opt/data/config.yaml ]]; then
  echo "Config not found! Generating..."
  exec /app/scripts/generate-config.sh
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