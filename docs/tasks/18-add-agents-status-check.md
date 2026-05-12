# Add agents status check via nats

Goal of this task is to implement proper agent status check through nats protocol in order to allow agent who delegated task to check for it's statys, and for agent who is executing delegated task, to send status update

## Definition of Done
1. `agent-to-agent` skill allows agent who delegated task to check status of it's task via `check_messages` action where it can see new message with newly added status `update` (additionaly to existing `completed` and `failed`), which includes details of update eg. "Task is still running"
2. `agent-to-agent` skill allows agent who is executing task to send status update via `update_status` action which results in sending new message with statys `update` to agent who delegated the task, along with udpate details of eg. "Task is still running"
4. `nats-helper.py` script contains implementation of `update_status` action and it's usage in `agent-to-agent` skill, along with new `update` status.
5. ``agent-to-agent/SKILL.md` is updated with additional information ensuring that when new task is being delegated, agent who receives it is obliged to post status updates every 3 minutes if task takes more than 5 minutes to complete. Status update should be done using new `update_status` action.
6. `skill-runner.mjs` is updated accordingly after changes.
7. All the changes are committed with proper commit messages.
8. Skill and scripts has tests written in TDD manner. Tests should be written before implementation based on prepared implementation plan, and fail, then after implementation they should pass. All tests should be done using actual docker containers


## Development rules and context
- use subagents to execute every step
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- always use TDD approach. Plan should start with failing tests first, and then implement functionality in a way it will make tests pass
- commit often, but do not push anything to remote, let user to handle that after impleentation finishes
- use up to date documentation from this year. Check current date
- test in docker container, not in local environment

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Skills are defined in `src/skills/`
- Use nats official documentation and ensure proper nats communication is established between agents, using sdks, and proper communication patterns according to official documentation (like subjects, queues, pub-sub, etc.)
- Use hermes agent offictial documentation for understanding how to check agent status without interrupting current task
