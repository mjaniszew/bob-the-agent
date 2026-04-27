# Agents

This file describes the agent architecture, roles, and communication patterns.

## Current Agent Architecture

The system uses a **hub-and-spoke** orchestration pattern with OpenClaw's multi-agent system:

```
                    ┌──────────────┐
                    │  Main Agent  │
                    │  (Orchestrator)│
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐────────────┐
              │            │            │            │
       ┌──────▼──────┐ ┌──▼───────┐ ┌──▼──────────┐ ┌──▼──────────────┐
       │ web-searcher │ │ research- │ │ data-       │ │ document-       │
       │              │ │ analyzer  │ │ extractor   │ │ creator         │
       └──────────────┘ └─────┬─────┘ └─────────────┘ └─────────────────┘
                             │
              ┌──────────────┼────────────┐
              │              │             │
      ┌───────▼──────┐ ┌────▼──────┐ ┌────▼──────────┐
      │ web-searcher │ │ data-     │ │ document-    │
      │              │ │ extractor │ │ creator      │
      └──────────────┘ └───────────┘ └──────────────┘
```

### Agents

| Agent | ID | Role | Model | Can Spawn |
|-------|----|------|-------|-----------|
| Main | `main` | Orchestrator — receives tasks, delegates to specialists, collects and synthesizes results | kimi-k2.5:cloud | All agents (`*`) |
| Web Searcher | `web-searcher` | Finds information on the web using SearXNG, X.com, and Grok | minimax-m2.7:cloud | None |
| Research Analyzer | `research-analyzer` | Researches topics and analyzes data by delegating search and extraction | kimi-k2.5:cloud | web-searcher, data-extractor, document-creator |
| Data Extractor | `data-extractor` | Extracts structured data from websites and documents | minimax-m2.7:cloud | None |
| Document Creator | `document-creator` | Creates PDF, DOCX, Markdown, and plain text documents | minimax-m2.7:cloud | None |

### Agent Hierarchy

- **Depth 0**: Main agent (orchestrator)
- **Depth 1**: Sub-agents spawned by main (web-searcher, research-analyzer, data-extractor, document-creator)
- **Depth 2**: Leaf agents spawned by research-analyzer (web-searcher, data-extractor, document-creator)

The `maxSpawnDepth` is set to 2, allowing the research-analyzer to delegate further. Leaf agents (depth 2) cannot spawn additional agents.

## Communication Pattern

### Sessions and Spawning

Agents communicate via OpenClaw's `sessions_spawn` tool:

1. **Main agent** analyzes the task and decides which specialist(s) to delegate to
2. Main spawns a sub-agent with a task prompt using `sessions_spawn`
3. Sub-agent executes the task and writes results to files in `/app/data/`
4. Sub-agent reports back the **file path** of results (not the content)
5. Main agent reads the results file, extracts what's needed, and continues

### Result Passing Protocol

**Critical**: Sub-agents must NEVER pass large content back to the orchestrator directly. They must:

1. Write results to files in `/app/data/{YYYY-MM-DD}/{task-id}/`
2. Create a `RESULTS.md` summary file with: task summary, output files list, key findings
3. Report back ONLY the file path(s) to the orchestrator
4. Create a `.manifest.json` listing all output files with descriptions

This prevents context window bloat and keeps the orchestrator efficient.

### Spawn Prompt Template

When spawning a sub-agent, the orchestrator MUST include:

```
Task: <description of what to do>
Result path: /app/data/YYYY-MM-DD/task-id/
Task type: <one-shot | recurring>
Save memory: <yes | no>
Current date: YYYY-MM-DD
Context: <relevant background information>
```

## Data Paths

| Path | Purpose | Used By |
|------|---------|---------|
| `/app/data/{YYYY-MM-DD}/{task-id}/` | Sub-agent working directory for task results | All sub-agents |
| `/app/results/` | Final output files delivered to user | Main agent only |
| `/app/user-files/` | Input files provided by user | Read by any agent |
| `{workspace}/memory/` | Agent memory files | All agents |
| `{workspace}/MEMORY.md` | Long-term curated memory | Main agent only |

## Memory and Learning

All agents maintain memory files for continuity across sessions:

- **Daily notes**: `memory/YYYY-MM-DD.md` — raw logs of what happened
- **Long-term memory** (main only): `MEMORY.md` — curated wisdom and lessons learned

Agents should:
- Save important findings and patterns to memory files during execution
- Check memory for relevant past findings before starting a task
- Mark recurring tasks and store useful reference data
- The orchestrator always informs sub-agents whether a task is recurring and what to save

## OpenClaw Configuration

The agent system is configured in `src/config/openclaw.template.json` with:

- `maxSpawnDepth: 2` — allows two levels of delegation
- `maxChildrenPerAgent: 5` — max sub-agents per parent
- `maxConcurrent: 8` — global concurrency cap
- Each agent has its own workspace and model configuration
- The main agent can spawn any other agent (`allowAgents: ["*"]`)
- Research-analyzer can spawn web-searcher, data-extractor, and document-creator

## Known Limitations

1. **SOUL.md not loaded for sub-agents**: OpenClaw bug — sub-agents only receive `AGENTS.md` and `TOOLS.md`, not `SOUL.md`. All critical behavioral rules must be placed in `TOOLS.md` or passed in the spawn prompt.
2. **Sub-agent results delivery**: Some OpenClaw versions have issues with results not being delivered back. Always use file-based result passing as the primary mechanism.
3. **`sessions_spawn` is non-blocking**: Returns immediately with `runId`. The orchestrator must check for results files or use `sessions_list`/`sessions_history` to monitor progress.