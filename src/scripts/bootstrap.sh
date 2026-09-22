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

pre_reconcile_backup() { # <config_path> — one-time snapshot of the pre-reconcile config
  # Created only if absent: the backup must capture the ORIGINAL (pre-reconcile)
  # state, so reboots after the first reconcile must never overwrite it with
  # already-reconciled content.
  [ -f "$1.pre-reconcile.bak" ] || cp "$1" "$1.pre-reconcile.bak" \
    || log "WARN: could not back up $1 before reconcile (continuing)"
}

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
else
  # Reconcile a pre-existing (e.g. migrated) config with the current partial:
  # deep-merge, partial wins on overlapping keys, user-only keys preserved.
  # Without this, a migrated config predating the single-container refactor
  # never gains gateway.multiplex_profiles (or any new partial keys).
  log "reconciling default (main) profile config with current partial"
  pre_reconcile_backup "$HERMES_HOME/config.yaml"
  node /app/scripts/merge-yaml.mjs "$HERMES_HOME/config.yaml" \
    "$AGENTS_DIR/main/hermes.partial.yml" "$HERMES_HOME/config.yaml" \
    || log "WARN: main config reconciliation failed; keeping existing config"
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
  else
    # Same reconcile-as-main rationale: keep existing profile configs aligned
    # with the current partial (e.g. model wiring) without discarding keys
    # the partial does not manage.
    log "reconciling profile $name config with current partial"
    pre_reconcile_backup "$dir/config.yaml"
    node /app/scripts/merge-yaml.mjs "$dir/config.yaml" \
      "$AGENTS_DIR/$name/hermes.partial.yml" "$dir/config.yaml" \
      || log "WARN: profile $name config reconciliation failed; keeping existing config"
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

# ---- 4. Default Ollama model -------------------------------------------------
# The ollama container ships empty and (unauthenticated) cannot serve the
# ollama.com cloud proxy used by "<model>:cloud" names, so every profile must
# be able to resolve the template's local default model on first boot. Pull is
# best-effort and non-fatal: the gateway starts regardless (ollama retries/
# errors surface per-request), but a successful pull here keeps the first
# real model call from being a 404.
OLLAMA_MODEL="$(node -e "const {createRequire}=require('module');const r=createRequire('/app/scripts/merge-yaml.mjs');const y=r('js-yaml');const c=y.load(require('fs').readFileSync('$TEMPLATE_FILE','utf8'));process.stdout.write((c&&c.model&&c.model.default)||'')" 2>/dev/null || true)"
# Fallback mirrors hermes.template.yaml's model.default (also pinned in
# src/agents/*/hermes.partial.yml); only used if the template is unreadable.
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:2b-q4_K_M}"
# Timeouts keep a stalled ollama connection from hanging boot forever: the
# tags probe must answer within 10s; the pull itself (first boot downloads a
# ~1.6GB blob) is capped at 30 minutes.
if ! curl -fsS --connect-timeout 10 --max-time 10 "${OLLAMA_BASE_URL:-http://ollama:11434}/api/tags" 2>/dev/null | grep -q "\"$OLLAMA_MODEL\""; then
  log "pulling ollama model: $OLLAMA_MODEL"
  PULL_OUT="$(mktemp)"
  PULL_ERR="$(mktemp)"
  if curl -fsS --connect-timeout 10 --max-time 1800 "${OLLAMA_BASE_URL:-http://ollama:11434}/api/pull" \
    -H 'Content-Type: application/json' -d "{\"model\":\"$OLLAMA_MODEL\"}" \
    >"$PULL_OUT" 2>"$PULL_ERR" \
    && grep -o '"status":"success"' "$PULL_OUT" >/dev/null; then
    log "pulled ollama model: $OLLAMA_MODEL"
  else
    # Keep ollama's error body (JSON on stdout) or curl's error (stderr)
    # visible in the WARN — a bare "could not pull" is undiagnosable.
    log "WARN: could not pull ollama model $OLLAMA_MODEL (continuing); last error: $(tail -n 1 "$PULL_OUT" 2>/dev/null || true) $(tail -n 1 "$PULL_ERR" 2>/dev/null || true)"
  fi
  rm -f "$PULL_OUT" "$PULL_ERR"
else
  log "ollama model present: $OLLAMA_MODEL"
fi

# ---- 5. Shared workspaces ---------------------------------------------------
mkdir -p /app/projects /app/results

# ---- 6. Exec the long-running gateway ---------------------------------------
# s6-overlay stays PID 1 (entrypoint-dispatch.sh execs /init). This command runs
# as a child of the already-running s6; exec-ing the gateway makes the container's lifetime
# equal to the gateway process — the documented foreground daemon command for
# Docker deployments. Bootstrap is idempotent, so a container restart re-runs
# provisioning safely before exec-ing the gateway again.
log "starting hermes gateway"
exec hermes gateway run
