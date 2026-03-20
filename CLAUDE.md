# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project builds a containerized AI agent that runs 24/7 autonomously via Docker/Docker Compose. The agent executes user-defined tasks without requiring interaction. Final artifacts are a Dockerfile and docker-compose.yml.

**Mandatory reading before starting work:**
1. `AGENTS.md` - General instructions for all AI agents. Read it first before anything else.
2. `docs/OVERVIEW.md` - Project purpose and development workflow

## Architecture

- **docs/features/** - Feature specifications that serve as additional context when building new capabilities:
  - `GENERAL.md` - Autonomy, CLI, Discord bot, web interface requirements
  - `PROVIDERS.md` - Ollama integration and model provider configuration
  - `SKILLS.md` - Web search, data extraction, math operations, document creation, notifications, scheduling
- **docs/tasks/** - Specific task definitions to execute (work on one task at a time when specified)
- **dockerfiles/** - Docker-related build files

## Key Requirements

- Agent must run fully autonomously - never ask questions during execution
- Use stable, community-endorsed solutions (Claude Code, OpenClaw, Ollama)
- Support multiple model providers with cloud API keys via environment variables
- Mountable external volumes for result files and user-provided files
- Lightweight enough for mediocre mini PCs

## Development Rules from AGENTS.md

- Never read, use, or modify `.env` or `/env.*` files
- Perform actions (reading files, web search, planning, execution) as separate subagents
- Do not read files in `docs/` unless explicitly asked or stated in OVERVIEW.md
- Run shell commands relating to agent workspace without asking permission