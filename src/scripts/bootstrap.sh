#!/usr/bin/env bash
# Bob the Agent — single-container bootstrap.
# Provisions the default profile (main orchestrator) + three secondary
# Hermes profiles, then execs the long-running gateway (hermes gateway run).
# s6-overlay stays PID 1 (image entrypoint entrypoint-dispatch.sh execs /init,
# which runs this script as its main program via main-wrapper.sh — dropped to
# the `hermes` user, HOME=/opt/data, hermes venv already on PATH).
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/opt/data}"
TEMPLATE_FILE="/app/config/hermes.template.yaml"
AGENTS_DIR="/app/agents"
SKILLS_SRC="/app/skills"
PROFILE_NAMES="researcher simple coder"

log() { echo "[bootstrap] $*"; }

write_profile_env() {
  # Per-profile secrets scope (Hermes never inherits root .env into profiles).
  # umask 077 in a subshell so the file is never world-readable even in the
  # window between creation and the chmod below.
  ( umask 077
    cat > "$1/.env" <<EOF
OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://ollama:11434}
SEARXNG_BASE_URL=${SEARXNG_BASE_URL:-http://searxng:8888}
NODE_ENV=production
USER_X_COM_API_TOKEN=${USER_X_COM_API_TOKEN:-}
USER_XAI_SEARCH_API_KEY=${USER_XAI_SEARCH_API_KEY:-}
USER_AWS_S3_BUCKET=${USER_AWS_S3_BUCKET:-}
USER_AWS_S3_REGION=${USER_AWS_S3_REGION:-}
USER_AWS_S3_ACCESS_KEY_ID=${USER_AWS_S3_ACCESS_KEY_ID:-}
USER_AWS_S3_SECRET_ACCESS_KEY=${USER_AWS_S3_SECRET_ACCESS_KEY:-}
EOF
  )
  chmod 600 "$1/.env"
}

copy_skills() { # <target_profile_dir> — skip node_modules/ and dist/ (volume bloat)
  mkdir -p "$1/skills"
  local d name
  for d in "$SKILLS_SRC"/*/; do
    [ -d "$d" ] || continue   # glob no-match safety: literal pattern would crash under set -e
    name="$(basename "$d")"
    [ "$name" = "node_modules" ] && continue
    [ "$name" = "dist" ] && continue
    cp -r "$d" "$1/skills/"
  done
}

# ---- 1. Default profile (main orchestrator) ------------------------------
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  log "generating default (main) profile config"
  /app/scripts/generate-config.sh "$TEMPLATE_FILE" "$AGENTS_DIR" main "$HERMES_HOME/config.yaml"
fi
[ -f "$HERMES_HOME/SOUL.md" ] || cp "$AGENTS_DIR/main/SOUL.md" "$HERMES_HOME/SOUL.md"
copy_skills "$HERMES_HOME"

# ---- 2. Secondary profiles ------------------------------------------------
for name in $PROFILE_NAMES; do
  dir="$HERMES_HOME/profiles/$name"
  if [ ! -f "$dir/config.yaml" ]; then
    log "provisioning profile: $name"
    mkdir -p "$dir"
    /app/scripts/generate-config.sh "$TEMPLATE_FILE" "$AGENTS_DIR" "$name" "$dir/config.yaml"
  fi
  # Idempotent if-missing writes, OUTSIDE the config guard: a boot that died
  # mid-provisioning must not leave a half-built profile the guard skips forever.
  [ -f "$dir/SOUL.md" ] || cp "$AGENTS_DIR/$name/SOUL.md" "$dir/SOUL.md"
  # Bot Mode roster marker (headless): ui_meta.hermes-bots
  [ -f "$dir/profile.yaml" ] || printf 'display_name: %s\nui_meta:\n  hermes-bots: {}\n' "$name" > "$dir/profile.yaml"
  write_profile_env "$dir"
  copy_skills "$dir"
  # Canonical Bot Chat session so message_agent exists for this profile
  # (best-effort: the gateway limitation is tracked upstream in issue #91260)
  hermes -p "$name" chat -c "Bot Chat" --create-if-missing >/dev/null 2>&1 || \
    log "WARN: could not create Bot Chat session for $name"
done

# ---- 3. OpenCode CLI config (coder two-layer) -----------------------------
# Path is fixed by compose env (OPENCODE_CONFIG) so docker exec can verify it —
# docker exec does NOT see exports performed inside this process.
# NOTE: this script runs as the `hermes` user (main-wrapper.sh drops
# privileges via s6-setuidgid) with HOME=/opt/data — never write under /root.
OPENCODE_TARGET="${OPENCODE_CONFIG:-/opt/data/.config/opencode/opencode.jsonc}"
mkdir -p "$(dirname "$OPENCODE_TARGET")"
sed -e "s|OLLAMA_BASE_URL_PLACEHOLDER|${OLLAMA_BASE_URL:-http://ollama:11434}|g" \
    "/app/config/opencode.template.jsonc" > "$OPENCODE_TARGET"

# ---- 4. Shared workspaces ---------------------------------------------------
mkdir -p /app/projects /app/results

# ---- 5. Exec the long-running gateway ---------------------------------------
# s6-overlay stays PID 1 (entrypoint-dispatch.sh execs /init). This command runs
# as a child of the already-running s6; exec-ing the gateway makes the container's lifetime
# equal to the gateway process — the documented foreground daemon command for
# Docker deployments. Bootstrap is idempotent, so a container restart re-runs
# provisioning safely before exec-ing the gateway again.
log "starting hermes gateway"
exec hermes gateway run