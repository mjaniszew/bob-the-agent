#!/bin/sh
set -e

echo "Building bob-the-agent image..."
docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .

echo "Starting stack..."
docker compose up -d

echo "Done!"