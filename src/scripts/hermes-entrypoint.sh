#!/bin/sh
set -e

echo "Agent starting"

# Generate config if does not exists
if [ ! -f /opt/data/config.yaml ]; then
  echo "Config not found! Generating..."
  exec /app/scripts/generate-config.sh
fi

# Set up skills directory for aget
if [ ! -d /opt/data/skills ]; then
  mkdir -p /opt/data/skills
fi

# Synchronize skill definitions to hermes skills directory if not already there
for skill_dir in /app/skills/*/; do
  if [ -d "$skill_dir" ]; then
    skill_name=$(basename "$skill_dir")
    mkdir -p "/opt/data/skills/$skill_name"
    if [ -f "${skill_dir}SKILL.md" ]; then
      cp "${skill_dir}SKILL.md" "/opt/data/skills/$skill_name/SKILL.md"
    fi
  fi
done

# Execute the main command
exec "$@"
