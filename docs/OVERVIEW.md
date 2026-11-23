# Project overview

This project purpose is to build containerized AI agent which can be run via Docker or Docker Compose, with freedom to perform any tasks 24/7 without any user interactions, based on tasks defined by user beforehand.

## Architecture

The project uses a **single-container architecture**. The orchestrator is a **custom Python harness** (thin, pydantic-AI based, `src/harness/`) replacing the former "main"-profile Hermes gateway for orchestration. One `agent` container runs:

- **Python harness** — gateway/UI (`:8181`, API-key secured), pydantic-AI **router brain** (picks agent + hermes profile per turn/task with keyword fallbacks), **Discord bot** (single identity, harness-owned), **task loop** (inbox queue with gates) and **cron scheduler**
- **Hermes profiles** (`/opt/data/profiles/`) — one-shot CLI workers invoked by the harness adapters, always with a provisioned profile (`hermes -p <profile>`):
  - **researcher** — deep research and analysis specialist
  - **simple** — lightweight handler for simple tasks (the default chat fallback profile)
- **OpenCode CLI** — the coding engine (`opencode run --format json` inside a validated workdir under `/app/projects`); there is no `coder` Hermes profile

Ollama serves LLM inference (local + ollama.com cloud proxy) and SearXNG provides search. Bootstrap (`src/scripts/bootstrap.sh`) provisions the profiles and harness state, then execs the harness directly (`python -m harness.supervisor` — s6 stays PID 1; the legacy per-profile Hermes gateway is held down by the `02-harness-only` cont-init hook so the harness bot owns the Discord identity).

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed system architecture, container configuration, and data flow.

## Features

- `docs/features` folder contains files describing specific functionalities that agent should have. Read it every time you're being asked to build something new, and use it as additional context to what you've been asked to build. Features might change over time and it's critical to keep track of changes in this folder

## Development

- your job as AI Agent is to build another ai agent with any means possible, according to requirements specified in a particular task pointed from `docs/tasks` folder at the time, as well as according to context of all features described in `docs/features`
- `docs/tasks` folder will contain specific tasks with detailed definition of what to build step by step. You will be asked to build something new every time, executing only one, specified by user task at the time, and ignoring rest of tasks from that folder

## Main assumptions and requirements

- agent should be able to run in container using Docker or Docker Compose
- it should be fully autonomic, able to run 24/7 according to tasks defined by user later on
- it should be based on current most popular and free or cheap solutions. Agent itself uses Hermes Agent framework (open-source, MIT licensed) for orchestration, and Ollama for model serving with support for both local and cloud models
- only select dependencies and solutions which are considered fairly stable, and already have some traction and are endorsed by community, do not use newly announced and unproven solutions
- agent should be fairly lightweight, possible to be launched on mediocre mini PC in containers, and use models from cloud
- final artifact of your work should be Dockerfile and docker-compose.yml (compose.yaml) which will allow to build and deploy agent in containers
