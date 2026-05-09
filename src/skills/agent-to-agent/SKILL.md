---
name: agent-to-agent
description: Use this skill to communicate with other agents via NATS messaging. Send tasks to specialized agents, check for incoming messages, and send task results back. Trigger words: agent-to-agent|nats|inter-agent|delegate|message agent
---

# Agent-to-Agent Communication Skill

Use this skill to communicate with other agents in the system via NATS messaging. This enables cross-container task delegation and result reporting between specialized agents.

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
1. **Simple Agent** (`simple`) - For general simple tasks. Use this agent when: 
 - use always for any web search
 - for documents creation that do not falls under other specialized agents eg. simple summaries
 - for basic data exctraction
 - for basic browser usage (screenshots, web pages scraping, basic web pages interactions)
 - for general tasks which does not require long context and specialized thinking
2. **Researcher Agent** (`researcher`) - For complex research, analysis and synthesis tasks. Use this agent when:
 - whenever task uses keywords like `research`, `analyze`, `synthezize`, `synthesis`
 - for complex tasks requiring deep analysis and synthesis
 - for cross referencing sources and informations
 - for creating comples research documents

## Delegation procedure
General delegation procedure for agents using this skill is that when you receive task, always do the following:
1. **Understand the task** - Make sure you understand the task requirements. If task is even moderately complex, has multiple steps and requires tools and skills usage, it will require plan which should cover delegation to specialized agents.
2. **Choose specialized agents** - This skill contains list of available specialized agents, and rules when to use them.
3. **Plan** - Create plan which should cover what and how to delegate to specialized agents. If plan consist of multiple steps, save it as a files for further use. For plan creation you can use `delegate_task` command to delegate to subagent.
4. **Delegate** - Execute plan delegating to proper specialized agents. When you delegate to agent, always spawn subagent directly using `delegate_task` command, and this subagent should be responsible for delegating further, waiting for results. and informing you about the progress and passing results.

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

- Messages are routed to specific agents using NATS subjects — only the targeted agent receives the message
- The background `register-nats.py` listener automatically executes incoming tasks and sends results back
- Results are persisted to `/opt/data/nats-messages/incoming/` and consumed on read (check_messages removes them)
- Use file-based result passing for large payloads (save to disk, reference path in message)
- The `save_results_to` parameter tells the receiving agent where to store its output files