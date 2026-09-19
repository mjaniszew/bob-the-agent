---
name: delegate-profile
description: Use this skill to delegate tasks to specialized agent profiles running in this same container. Delegates work by running `hermes -p <profile> chat --oneshot` in the foreground or as a background task with a log you can poll. Trigger words: delegate|agent-to-agent|subagent|delegate-profile
tags:
  - delegate
  - agent-to-agent
---

# Delegate-Profile Skill

Use this skill to delegate tasks to specialized agent profiles. All agents (simple, researcher, coder) are Hermes profiles in this same container — there is no NATS and no network transport; delegation is in-process via the `hermes` CLI.

## ⚠️ MANDATORY: DELEGATION FIRST PROTOCOL

**For EVERY user task, you MUST run this checklist BEFORE taking any action yourself:**

1. **Understand the task** — What does the user want? Does it involve search, research, coding, data extraction, or analysis?
2. **Load this skill** — You are reading it now. Check the "Available profiles" section below.
3. **Decide who does it** — Match the task to the right agent (see "Available profiles" below).
4. **Delegate via `delegate_task`** — Spawn a subagent and let it handle the work. **Do NOT execute the task yourself.**
5. **Wait for results** — The subagent will report back.

**If you skip this checklist and execute a task yourself that should have been delegated, you are violating your protocol.**

Common delegation triggers:
- Any web search → `simple` agent
- Files upload using skills → `simple` agent
- Keywords: "research", "analyze", "synthesize", "compare", "find sources" → `researcher` agent
- Keywords: "code", "implement", "refactor", "debug", "review code", "architecture", "fix bug" → `coder` agent
- Data extraction from web pages → `simple` agent
- Summaries, document creation → `simple` agent

## Available profiles

| Profile | target_agent_id | Timeout | Use for |
|---------|-----------------|---------|---------|
| Simple | `simple` | 15 minutes | Web search, simple docs/summaries, data extraction, basic browser usage, S3 uploads |
| Researcher | `researcher` | 60 minutes | Deep research, analysis and synthesis, cross-referencing sources |
| Coder | `coder` | 120 minutes | Coding, code review, architecture planning via the OpenCode CLI |

### **Simple Agent** (target_id: simple)
For general simple tasks: any kind of web search (`web_search`, `web_fetch`, `web_extract`, `searxng-web-search`, `searxng-search`), simple document creation and summaries, basic data extraction, basic browser usage (screenshots, scraping, simple interactions), uploading files to external services (e.g. AWS S3), and general tasks that do not require long context or specialized thinking. NOT for: complex/deep research tasks or coding tasks.

### **Researcher Agent** (target_id: researcher)
For complex research, analysis and synthesis tasks: keywords like `research`, `analyze`, `synthesize`, `synthesis`; cross-referencing sources and information; creating complex research documents. NOT for: basic tasks without deep analysis or coding tasks.

### **Coder Agent** (target_id: coder)
For software engineering tasks: coding (write code, implement features, refactor, fix bugs), code review, architecture planning and design, git operations on project repositories, and any task involving software development or code modification — via the OpenCode CLI. NOT for: simple web search (use `simple`) or deep research (use `researcher`).

## Usage

### Send a Task (foreground)

Run the target profile to completion in the foreground; the result summary is returned directly:

```bash
node /app/scripts/skill-runner.mjs --skill delegate-profile --params '{"action": "send_task", "target_agent_id": "researcher", "goal": "...", "context": "...", "save_results_to": "/app/results/..."}'
```

Prefer foreground `send_task` for tasks expected to finish under ~10 minutes.

### Background flow (long tasks)

For longer tasks, start in the background and poll:

1. `send_task_background` — returns `task_id` and `log_file`; the task runs detached, appending output to `log_file`
2. Poll `check_task` with the `task_id` — returns `status: "running"` while the task is in progress and `status: "finished"` once done, along with the last 4000 characters of the log (`log_tail`)

```bash
node /app/scripts/skill-runner.mjs --skill delegate-profile --params '{"action": "send_task_background", "target_agent_id": "coder", "goal": "...", "context": "...", "save_results_to": "/app/results/..."}'
node /app/scripts/skill-runner.mjs --skill delegate-profile --params '{"action": "check_task", "task_id": "<task_id from send_task_background>"}'
```

## Actions

| Action | Required params | Description |
|--------|-----------------|-------------|
| `send_task` | `action`, `target_agent_id`, `goal` | Run the task in the foreground; returns `{status: "completed", target, summary}` |
| `send_task_background` | `action`, `target_agent_id`, `goal` | Start the task detached; returns `{status: "started", task_id, target, log_file, pid}` |
| `check_task` | `action`, `task_id` | Poll a background task; returns `{status: "running" \| "finished", task_id, log_tail}` |

Optional params for `send_task` / `send_task_background`: `context` (additional background information), `save_results_to` (path where the target profile should save result files).

## Notes

- The orchestrator must attempt delegation (per the DELEGATION FIRST PROTOCOL above) before doing work itself.
- Valid targets are exactly: `simple`, `researcher`, `coder` — anything else is rejected.
- Timeouts are fixed per target (15m / 60m / 120m) and enforced by the skill.
- There is no messaging layer: background task state lives entirely in the log file and its `.running` marker under `/opt/data/delegation`.
- When polling a long task, also check `ls` on `save_results_to` — new files appearing there mean the task finished.