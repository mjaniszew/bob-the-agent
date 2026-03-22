#!/bin/sh
set -e

# Generate OpenClaw config if not exists
if [ ! -f /home/node/.openclaw/openclaw.json ]; then
  node /app/scripts/generate-config.mjs
fi

# Set up skills directory for OpenClaw
# OpenClaw looks for skills in ~/.openclaw/skills/ or /app/skills/
mkdir -p /home/node/.openclaw/skills

# Copy skill definitions to OpenClaw skills directory if not already there
# This ensures OpenClaw can discover the skills
for skill_dir in /app/skills/*/; do
  if [ -d "$skill_dir" ]; then
    skill_name=$(basename "$skill_dir")
    mkdir -p "/home/node/.openclaw/skills/$skill_name"
    if [ -f "${skill_dir}SKILL.md" ]; then
      cp "${skill_dir}SKILL.md" "/home/node/.openclaw/skills/$skill_name/SKILL.md"
    fi
  fi
done

# Ensure data directory exists for scheduling database
mkdir -p /app/data

# Execute the main command
exec "$@"