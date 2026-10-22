# Agents

This file describes the agent architecture, profiles, and delegation patterns.

## Current Agent Architecture

The system runs on **Hermes** in a **single Docker image / single container** (base image `nousresearch/hermes-agent:v2026.9.14`, Hermes v0.21.3; s6 is PID 1 and the container CMD is `/app/scripts/bootstrap.sh`). All agent profiles live in that one container — there are no per-agent containers.

A single gateway multiplexes the profiles (in-process delegation via the hermes CLI):

```
┌────────────── bob-the-agent (single container) ──────────────┐
│                                                              │
│   s6 (PID 1) → CMD: /app/scripts/bootstrap.sh                │
│                                                              │
│   Gateway ── port 8642                                       │
│   │  (single gateway; multiplex_profiles: true)              │
│   │ multiplexes profiles:                                    │
│   ├── main       (default; orchestrator + Discord)           │
│   ├── simple                                                 │
│   ├── researcher                                             │
│   └── coder      (drives OpenCode CLI in-process)            │
│                                                              │
│   Dashboard (s6 service) ── port 9119                        │
└──────────────────────────────────────────────────────────────┘
```

### Profiles

Model overrides are per-request, defined in `src/config/hermes.template.yaml` plus each profile's `hermes.partial.yml`:

| Profile | Default | Model | `ollama_num_ctx` |
|---------|---------|-------|------------------|
| `main` | default profile (orchestrator + Discord) | `glm-5.3-flash:cloud` | 256000 |
| `simple` | | `glm-5.3-flash:cloud` | 65536 |
| `researcher` | | `glm-5.3:cloud` | 256000 |
| `coder` | | `glm-5.3-flash:cloud` | 256000 |

Notes:

- GLM limits: context 512000, output 32000.
- Compose sets `OLLAMA_CONTEXT_LENGTH` (default `65536`), while `hermes.partial.yml` pins `ollama_num_ctx` to `256000` for `main`/`coder`/`researcher` and `65536` for `simple` — a real tension between the two values; both are stated here as they are.
- Profile behavior is defined in each profile's `SOUL.md`. The `src/agents/{name}/` directories contain **only** `SOUL.md` + `hermes.partial.yml` — there are no per-profile `IDENTITY.md`, `AGENTS.md`, or `TOOLS.md` files.

### Gateway

- A **single gateway** multiplexes all profiles on port **8642**. Multiplexing is enabled globally via `gateway.multiplex_profiles: true` in `src/config/hermes.template.yaml` (it applies to the single gateway; it is **not** set in the main `hermes.partial.yml`).
- Compose publishes ports plainly as `8642:8642` and `9119:9119`.
- The container healthcheck is a TCP check on 8642. Gateway endpoints: `/health` and `/v1/health` answer `200`; `/healthz` returns `404`.

### Dashboard

- The dashboard is a dedicated **s6 service** on port **9119**, gated on the `HERMES_DASHBOARD` environment variable.
- Non-loopback binds require authentication: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` / `HERMES_DASHBOARD_BASIC_AUTH_SECRET`, or OAuth.

## Delegation

Delegation between profiles is **in-process** via the hermes CLI. There is no NATS message bus and no per-agent containers (e.g. `bob-the-agent-researcher`) — all profiles run inside the single container described above.

## OpenCode (coder profile)

The `coder` profile drives the **OpenCode CLI in-process**. OpenCode is pinned to `opencode-ai@1.18.32` in the Dockerfile (a deliberate pin; docs claiming `@latest` are stale).

The OpenCode template defines the following agents:

| OpenCode agent | Primary | Notes |
|----------------|---------|-------|
| `build` | `glm-5.3-flash:cloud` | |
| `plan` | `glm-5.3:cloud` | |
| `code-reviewer` | `kimi-k2.7-code:cloud` | read-only: write/edit tools disabled |
| `general`, `explore`, `scout` (subagents) | `glm-5.3-flash:cloud` | |

OpenCode config is written at bootstrap to `/opt/data/.config/opencode/opencode.jsonc` (compose sets `OPENCODE_CONFIG` to that path).

## bootstrap.sh

At container start, the bootstrap script (`/app/scripts/bootstrap.sh`, the container CMD) performs:

1. **Generate-or-reconcile** per profile, with a one-time `config.yaml.pre-reconcile.bak` snapshot
2. Copies `SOUL.md` + skills (skipping `node_modules`/`dist`)
3. Writes the `profile.yaml` bot roster (`ui_meta.hermes-bots`) and per-profile `.env` (umask 077 then chmod 600)
4. Best-effort **Bot Chat session** per profile (upstream Hermes issue 91260)
5. Writes the OpenCode config **unconditionally** to `/opt/data/.config/opencode/opencode.jsonc`
6. Best-effort `ollama pull` capped at 30 minutes, target = the template default `glm-5.3-flash:cloud` (a **cloud manifest** pull, not a ~1.6GB local download)

## Relevant Paths

| Path | Purpose |
|------|---------|
| `/app/scripts/bootstrap.sh` | Bootstrap script; container CMD |
| `src/config/hermes.template.yaml` | Template holding the profile model set, `gateway.multiplex_profiles: true`, and the default ollama model |
| `src/agents/{name}/` | Per-profile `SOUL.md` + `hermes.partial.yml` (only) |
| `/opt/data/.config/opencode/opencode.jsonc` | OpenCode config written at bootstrap; `OPENCODE_CONFIG` points here |

## Known Limitations

1. **Per-request ollama context mismatch**: `hermes.partial.yml` pins `ollama_num_ctx` to `256000` for `main`/`coder`/`researcher`, while compose's `OLLAMA_CONTEXT_LENGTH` defaults to `65536` — the real values are stated above and this tension exists as such.
2. **Bot Chat session bootstrap is best-effort**: creation of the per-profile Bot Chat session can fail (tracked upstream as Hermes issue 91260).
3. **OpenCode is pinned, not floating**: the Dockerfile pins `opencode-ai@1.18.32`; docs claiming `@latest` are stale.