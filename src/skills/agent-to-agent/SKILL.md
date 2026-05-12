---
name: agent-to-agent
description: Use this skill to communicate with other agents via NATS messaging. Send tasks to specialized agents, check for incoming messages, and send task results back. Trigger words: agent-to-agent|nats|inter-agent|delegate|message agent
tags:
  - delegate
  - agent-to-agent
---

# Agent-to-Agent Communication Skill

Use this skill to communicate with other agents in the system via NATS messaging. This enables cross-container task delegation and result reporting between specialized agents.

## ⚠️ MANDATORY: DELEGATION FIRST PROTOCOL

**For EVERY user task, you MUST run this checklist BEFORE taking any action yourself:**

1. **Understand the task** — What does the user want? Does it involve search, research, coding, data extraction, or analysis?
2. **Load this skill** — You are reading it now. Check the "Available agents" section below.
3. **Decide who does it** — Match the task to the right agent (see "Available agents" below).
4. **Delegate via `delegate_task`** — Spawn a subagent and let it handle the work. **Do NOT execute the task yourself.**
5. **Wait for results** — The subagent will report back.

**If you skip this checklist and execute a task yourself that should have been delegated, you are violating your protocol.**

Common delegation triggers:
- Any web search → `simple` agent
- Keywords: "research", "analyze", "synthesize", "compare", "find sources" → `researcher` agent
- Data extraction from web pages → `simple` agent
- Summaries, document creation → `simple` agent

## When to Use

Use this skill when:
- You need to delegate a task to another agent (e.g., researcher, simple-agent)
- You need to check if there are any incoming task messages for the current agent
- You need to send a task result back to the agent that delegated a task

## When NOT to Use

Do NOT use this skill for:
- Delegating tasks to sub-agents within the same container (use Hermes `delegate_task` instead)
- File operations (use terminal commands directly)

## Available agents

Following specialized agents are available:
### **Simple Agent** (target_id: simple)
For general simple tasks.

When to Use:
 - use always for any kind of web search: `web_search`, `web_fetch`, `web_extract`, `searxng-web-search`, `searxng-search`
 - for documents creation that do not falls under other specialized agents eg. simple summaries
 - for basic data exctraction
 - for basic browser usage (screenshots, web pages scraping, basic web pages interactions)
 - for general tasks which does not require long context and specialized thinking

When NOT to Use:
 - Task is complex, long context or requires specialized thinking like deep research
 - for coding tasks

### **Researcher Agent** (target_id: researcher)
For complex research, analysis and synthesis tasks. 

When to Use:
 - whenever task uses keywords like `research`, `analyze`, `synthezize`, `synthesis`
 - for complex tasks requiring deep analysis and synthesis
 - for cross referencing sources and informations
 - for creating comples research documents

When NOT to Use:
 - basic tasks that do not require deep analysis and synthesis
 - for coding tasks

## Architecture

Agents communicate via NATS subjects:
- `agent.{target_id}.tasks` — Send a task to a specific agent
- `agent.{target_id}.results` — Receive results from a specific agent

Each agent runs a background `register-nats.py` listener that:
1. Subscribes to its own task subject
2. Executes incoming tasks via Hermes
3. Sends results back to the originating agent

## Usage

### Send a Task

```bash
node /app/scripts/skill-runner.mjs --skill agent-to-agent --params '{
  "action": "send_task",
  "target_agent_id": "researcher",
  "goal": "Research quantum computing breakthroughs in 2026",
  "context": "User requested a comprehensive report on recent advances",
  "toolsets": ["terminal", "file"],
  "save_results_to": "/app/data/2026-05-08/quantum-research/"
}'
```

### Check for Incoming Messages

```bash
node /app/scripts/skill-runner.mjs --skill agent-to-agent --params '{
  "action": "check_messages"
}'
```

### Send a Result

```bash
node /app/scripts/skill-runner.mjs --skill agent-to-agent --params '{
  "action": "send_result",
  "target_agent_id": "main",
  "original_message_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "summary": "Research completed. Key findings: ...",
  "result_path": "/app/data/2026-05-08/quantum-research/RESULTS.md"
}'
```

## Actions

### send_task

Send a task to a target agent via NATS.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | Must be `"send_task"` |
| target_agent_id | string | Yes | ID of the agent to send the task to (e.g., `researcher`, `simple`) |
| goal | string | Yes | Description of the task to perform |
| context | string | No | Additional context or background information |
| toolsets | string | No | Comma-separated list of toolsets to enable for the task |
| save_results_to | string | No | Path where the target agent should save results |

**Available target agents:**
- `researcher` — Deep research and analysis specialist
- `simple` — Lightweight handler for simple tasks

### check_messages

Check for pending incoming messages addressed to the current agent. Returns any unconsumed messages from the local message store.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| action | string | Yes | - | Must be `"check_messages"` |
| timeout | number | No | 0 | Seconds to wait for messages (0 = non-blocking poll) |

### send_result

Send a task result back to the originating agent.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | Must be `"send_result"` |
| target_agent_id | string | Yes | ID of the agent to send the result to |
| original_message_id | string | Yes | Message ID of the original task message |
| status | string | Yes | `"completed"` or `"failed"` |
| summary | string | No | Summary of the task result |
| result_path | string | No | Path to the result file(s) |
| error | string | No | Error message if task failed |

## Output

### send_task Response

```json
{
  "success": true,
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "subject": "agent.researcher.tasks",
  "sender_agent_id": "main",
  "target_agent_id": "researcher"
}
```

### check_messages Response

```json
{
  "success": true,
  "messages": [
    {
      "message_id": "...",
      "task_type": "delegate_task",
      "sender_agent_id": "main",
      "target_agent_id": "researcher",
      "timestamp": "2026-05-08T14:30:00+00:00",
      "payload": {
        "goal": "Research quantum computing",
        "context": "...",
        "toolsets": ["terminal", "file"],
        "save_results_to": "/app/data/..."
      }
    }
  ],
  "count": 1
}
```

### send_result Response

```json
{
  "success": true,
  "message_id": "660e8400-e29b-41d4-a716-446655440001",
  "subject": "agent.main.results",
  "sender_agent_id": "researcher",
  "target_agent_id": "main"
}
```

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| NATS connection error | NATS server unreachable | Check that the NATS container is running and NATS_URL is configured |
| Missing target_agent_id | Required param not provided | Always specify target_agent_id for send_task and send_result |
| Missing goal | Required param for send_task | Provide a clear task description |
| Message store empty | No messages pending | Use check_messages after a task delegation to get results |

## Notes

- IMPORTANT: always wait for a task completion for at least 5-6 minutes. If no results came back, send another message to same agent asking for status before you decide whether task timed out
- Messages are routed to specific agents using NATS subjects — only the targeted agent receives the message
- The background `register-nats.py` listener automatically executes incoming tasks and sends results back
- Results are persisted to `/opt/data/nats-messages/incoming/` and consumed on read (check_messages removes them)
- Use file-based result passing for large payloads (save to disk, reference path in message)
- The `save_results_to` parameter tells the receiving agent where to store its output files