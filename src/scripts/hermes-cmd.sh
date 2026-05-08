#!/bin/sh
exec /usr/bin/tini -s -- /opt/hermes/docker/entrypoint.sh "$@"
