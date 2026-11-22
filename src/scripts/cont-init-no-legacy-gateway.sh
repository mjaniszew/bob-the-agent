#!/command/with-contenv sh
# Task 22 policy: the Python harness (python -m harness.supervisor) is this
# container's ONLY orchestrator. The legacy per-profile Hermes gateway must
# never auto-start: it multiplexes the profiles' own Discord platforms and its
# bot competes with the harness bot for the same Discord identity (exactly the
# "bot reports online but messages never reach the harness" failure).
#
# cont-init.d/02-reconcile-profiles auto-starts the gateway-default s6 slot
# when the persisted gateway_state.json says "running" (a leftover of the old
# Hermes-only architecture, surviving on the /opt/data volume). This hook is
# named 02-harness-only so it sorts BEFORE 02-reconcile-profiles and clears
# that state first; the reconciler then sees "no prior state" and leaves every
# gateway slot DOWN.
#
# Runs each boot: a manually started legacy gateway stays reachable until the
# next container recreation, but it never comes back by itself.
set -eu

hermes_home="${HERMES_HOME:-/opt/data}"
rm -f "$hermes_home/gateway_state.json"
rm -f "$hermes_home"/profiles/*/gateway_state.json 2>/dev/null || true
echo "[harness-only] legacy hermes gateway state cleared (orchestrator: python harness)"
