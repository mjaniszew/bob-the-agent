#!/bin/sh
set -e
if [ ! -f /home/node/.openclaw/openclaw.json ]; then
  node /app/scripts/generate-config.mjs
fi
exec "$@"