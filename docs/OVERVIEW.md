# Project overview

This project purpose is to build containerized AI agent which can be run via Docker or Docker Compose, with freedom to perform any tasks 24/7 without any user interactions, based on tasks defined by user beforehand.

## Architecture

The project uses a **single-container architecture**. The orchestrator is a **custom Python bob-harness** (thin, pydantic-AI based, `src/bob-harness/`) replacing the former "main"-profile Hermes gateway for orchestration. One `agent` container runs:

- **bob-harness** (Python) — gateway/UI (`:8181`, API-key secured), pydantic-AI **router brain** (picks agent + hermes profile per turn/task with keyword fallbacks), **Discord bot** (single identity, bob-harness-owned), **task loop** (inbox queue with gates) and **cron scheduler**
- **Hermes profiles** (`/opt/data/hermes/profiles/`) — one-shot CLI workers invoked by the bob-harness adapters, always with a provisioned profile (`hermes -p <profile>`):
  - **researcher** — deep research and analysis specialist
  - **simple** — lightweight handler for simple tasks (the default chat fallback profile)
- **OpenCode CLI** — the coding engine (`opencode run --format json` inside a validated workdir under `/app/projects`); there is no `coder` Hermes profile

Ollama serves LLM inference (local + ollama.com cloud proxy) and SearXNG provides search. Bootstrap (`src/scripts/bootstrap.sh`) provisions the profiles and bob-harness state, then execs bob-harness directly (`python -m bob_harness.supervisor` — s6 stays PID 1; the legacy per-profile Hermes gateway is held down by the `02-bob-harness-only` cont-init hook so the bob-harness bot owns the Discord identity).

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed system architecture, container configuration, and data flow.

## Features

- `docs/features` folder contains files describing specific functionalities that agent should have. Read it every time you're being asked to build something new, and use it as additional context to what you've been asked to build. Features might change over time and it's critical to keep track of changes in this folder

## Development

- your job as AI Agent is to build another ai agent with any means possible, according to requirements specified in a particular task pointed from `docs/tasks` folder at the time, as well as according to context of all features described in `docs/features`
- `docs/tasks` folder will contain specific tasks with detailed definition of what to build step by step. You will be asked to build something new every time, executing only one, specified by user task at the time, and ignoring rest of tasks from that folder

## Pydantic AI v2 Migration (task 10, branch custom-harness-poc)

The bob-harness now runs on **pydantic-ai 2.73.0** with the harness capability
stack (**pydantic-ai-harness[coder]==0.73.0**), migrated over phases P0-P9 of
`docs/plans/10-pydantic-ai-v2-migration-plan.md`. Frozen pin set (see
`src/bob-harness/requirements.lock`): `pydantic-ai==2.73.0`,
`pydantic-ai-slim==2.73.0`, `pydantic-graph==2.73.0`, `pydantic-evals==2.73.0`,
`pydantic-ai-harness[coder]==0.73.0`, `json-repair==0.64.9`,
`pydantic==2.14.4`, `pydantic-core==2.50.5`.

What changed, in brief:

- **Router** (`RouterBrain`) is ported to v2 `Agent`s — capability-free, schema
  outputs, keyword fallback and `router_ledger.jsonl` contract unchanged (D7).
- **Executor adapter** (new, default `tasks.runner: executor` since P7): one
  v2 `Agent` per run wrapped behind the existing `AgentRun` protocol, with
  harness capabilities — Memory (shared FileMemoryStore, D4), Skills (existing
  SKILL.md folders, engine still owns INDEX.md, D5), AskUser (deferred-tool
  gate with a persisted resume seed, D3), nested SubAgents and a custom
  `delegate_run` capability that keeps hermes/opencode REAL subprocess CLI
  adapters (D1). The harness coder capabilities are NOT adopted (D6).
- **WAITING_INPUT gate** is rebuilt on the v2 deferred-tool mechanism;
  parked questions survive restarts (D3). FSM, `gates.jsonl`, timers, API and
  the Discord/gateway UI surfaces are unchanged (P6/P7).
- AskUser capability events are mirrored onto the Event Bus topics
  `harness.ask_user.requested` / `harness.ask_user.answered` (P8).
- Rollback lever for the one behavior flip (P7): `BOB_HARNESS_TASKS_RUNNER=analyzed`.

Detailed architecture: the "Pydantic AI v2 Architecture" section of
[ARCHITECTURE.md](./ARCHITECTURE.md) (D1-D7 rationale + capability manifest);
configuration: the "Harness Capability Configuration" section of
[CONFIGURATION.md](./CONFIGURATION.md) (the `harness:` group + flat
`BOB_HARNESS_*` env table).

## Main assumptions and requirements

- agent should be able to run in container using Docker or Docker Compose
- it should be fully autonomic, able to run 24/7 according to tasks defined by user later on
- it should be based on current most popular and free or cheap solutions. Agent itself uses Hermes Agent framework (open-source, MIT licensed) for orchestration, and Ollama for model serving with support for both local and cloud models
- only select dependencies and solutions which are considered fairly stable, and already have some traction and are endorsed by community, do not use newly announced and unproven solutions
- agent should be fairly lightweight, possible to be launched on mediocre mini PC in containers, and use models from cloud
- final artifact of your work should be Dockerfile and docker-compose.yml (compose.yaml) which will allow to build and deploy agent in containers
