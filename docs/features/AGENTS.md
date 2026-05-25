# Agents

This file describes the agent architecture, roles, and communication patterns.

## Current Agent Architecture

The system uses a **hub-and-spoke** orchestration pattern with OpenClaw's multi-agent system:

```
                    ┌────────────────┐
                    │  Main Agent    │
                    │  (Orchestrator)│
                    └──────┬─────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────▼──────┐ ┌─────▼───────┐ ┌─────▼──────────┐
    │ simple      │ │ researcher  │ │ coder          │
    │             │ │             │ │ ┌────────────┐ │
    │             │ │             │ │ │Hermes +    │ │
    │             │ │             │ │ │OpenCode CLI│ │
    └─────────────┘ └──────┬──────┘ │ └────────────┘ │
                          │        └────────────────┘
                          │
                   ┌──────▼──────┐
                   │ simple      │
                   └─────────────┘
```

### Agents

| Agent | ID | Role | Model | Can Spawn |
|-------|----|------|-------|-----------|
| Main | `main` | Orchestrator — receives tasks, delegates to specialists, collects and synthesizes results | kimi-k2.6:cloud | All agents (`*`) |
| Simple Agent | `simple` | Finds information on the web using SearXNG, X.com, and Grok, handles simple tasks | minimax-m2.7:cloud | None |
| Researcher | `researcher` | Researches topics and analyzes data by delegating search and extraction | kimi-k2.6:cloud | simple |
| Coder | `coder` | Handles Software engineering tasks: coding, review, design | glm-5.1:cloud | simple |

### Agent Hierarchy

- **Depth 0**: Main agent (orchestrator)
- **Depth 1**: Sub-agents spawned by main (simple, researcher, coder)
- **Depth 2**: Leaf agents spawned eg. by researcher (simple)

The `maxSpawnDepth` is set to 2, allowing the research-analyzer to delegate further. Leaf agents (depth 2) cannot spawn additional agents.

## Communication Pattern

### Sessions and Spawning

Agents communicate via `agent-to-agent` skill:

1. **Main agent** analyzes the task and decides which specialist(s) to delegate to
2. Main spawns a sub-agent with a task prompt using `delegate_task`, that sub-agent is responsible for delegating to agent using `agent-to-agent` skill, and coordinates tasks between agents
3. Agent executes the task and writes results to files in `/app/results/`
4. Agent reports back the **file path** of results (not the content)
5. Main agent reads the results file, extracts what's needed, and continues

## Data Paths

| Path | Purpose | Used By |
|------|---------|---------|
| `/app/results/` | Final output files produced by agents | All agents |


## Memory and Learning

All agents maintain memory files for continuity across sessions:

- **Daily notes**: `memory/YYYY-MM-DD.md` — raw logs of what happened
- **Long-term memory** (main only): `MEMORY.md` — curated wisdom and lessons learned

Agents should:
- Save important findings and patterns to memory files during execution
- Check memory for relevant past findings before starting a task
- Mark recurring tasks and store useful reference data
- The orchestrator always informs sub-agents whether a task is recurring and what to save


## NATS Inter-Agent Communication

Agents communicate across containers via **NATS** messaging, enabling the orchestrator to delegate tasks to specialized agents in separate Docker containers.

### Architecture

Each agent container runs a `register-nats.py` background listener alongside the Hermes gateway. This listener:
- Subscribes to NATS subjects for incoming tasks and results
- Executes incoming tasks via Hermes one-shot mode
- Publishes results back to the originating agent

### Message Flow

1. **Main agent** calls `agent-to-agent` skill with `send_task` action
2. **Skill** publishes a task message to `agent.{target_id}.tasks`
3. **Target agent's** `register-nats.py` receives the message
4. **Target agent** executes the task via `hermes -z "<prompt>"`
5. **Target agent** publishes result to `agent.{sender_id}.results`
6. **Main agent** checks for results via `check_messages` action

### NATS Subjects

| Subject | Purpose |
|---------|---------|
| `agent.{target_id}.tasks` | Send task to specific agent |
| `agent.{target_id}.results` | Receive results from specific agent |

### Available Target Agents

| Agent ID | Container | Role |
|----------|-----------|------|
| `researcher` | bob-the-agent-researcher | Deep research and analysis |
| `simple` | bob-the-agent-simple | Lightweight simple tasks |
| `coder` | bob-the-agent-coder | Software engineering (coding, review, architecture) |

### Configuration

- `NATS_URL` environment variable (default: `nats://nats:4222`)
- `NATS_TASK_TIMEOUT` environment variable (default: `600` seconds)
- Each agent subscribes with a queue group for horizontal scaling support

## Known Limitations

1. **SOUL.md not loaded for sub-agents**: OpenClaw bug — sub-agents only receive `AGENTS.md` and `TOOLS.md`, not `SOUL.md`. All critical behavioral rules must be placed in `TOOLS.md` or passed in the spawn prompt.
2. **Sub-agent results delivery**: Some OpenClaw versions have issues with results not being delivered back. Always use file-based result passing as the primary mechanism.
3. **`sessions_spawn` is non-blocking**: Returns immediately with `runId`. The orchestrator must check for results files or use `sessions_list`/`sessions_history` to monitor progress.