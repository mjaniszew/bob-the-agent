#!/bin/sh
exec /usr/bin/tini -g -- /opt/hermes/docker/entrypoint.sh $@
