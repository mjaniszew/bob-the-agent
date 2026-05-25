# Project overview

This project purpose is to build containerized AI agent which can be run via Docker or Docker Compose, with freedom to perform any tasks 24/7 without any user interactions, based on tasks defined by user beforehand.

## Architecture

The project uses a **multi-container architecture** built on the Hermes Agent framework. Each specialized agent runs in its own container:

- **agent-main** — Orchestrator that delegates tasks and provides the Discord bot interface
- **researcher** — Deep research and analysis specialist
- **simple-agent** — Lightweight handler for simple tasks
- **coder** — Software engineering specialist using a two-layer architecture: Hermes Agent (supervisor/bridge) orchestrating OpenCode CLI (coding engine)

All agents share an **Ollama** instance for LLM inference and a **SearXNG** search engine. Communication between agents uses Hermes' built-in `delegate_task` system and **NATS** inter-agent messaging for cross-container delegation.

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