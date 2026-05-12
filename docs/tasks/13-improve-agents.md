# Improve Agents

Goal of this task is to analyze current agents definitions in `src/agents` and improve achieving specific goals like: main agent being ochestrator for other agents, all agents are being used properly for particular tasks, all agents leave their output in a way that can be easily consumed by other agents an main agent, etc.

## Definition of Done
1. Current agents implementation is analyzed and summarized in details as md file in `docs/features/AGENTS.md`. Agents are working in openclaw.
2. Main agent in `src/agents/main` is defined as an effective subagents orchestrator. Any oher agent is threated as subagent. Main agent always makes sure that all subagents are used properly for particular tasks, and it gets results from subagents in order to proceed firther and/or pass to another subaget for further steps.
3. Subagents are working according to openclaw documentation in a way that they always produce results files and report back those results files to main agent instead of passing entire results and context directly. Main agent waits for results and also checks for new results files periodically if subagents are timing out or do not report back for too long.
4. All subagents are using `/app/data` folder for storing data and results. Main agent stores final results in `/app/results`. All agents and subagents uses proper subfolders for each of tasks to work on data needed for particular task execution.
5. All agents and subagents are able to analyze their task execution context and decide whether to store any memory data for future purposes eg. if task is being run periodically, or tasks specifics suggest that some data will be needed in the future like logs, web findings, trusted sources for specific informations found on the web etc.
6. Main agent always informs subagents about the context of task execution, whether it's reccuring task, whether some data should be saved for the future eg. in MEMORY.md file etc.
7. All agents and subagents are by default trying to learn and evolve, remembering as much as possible for future tasks.
8. Main agent always cleans context as much as possible in order to stay cost efficient eg. if subagent reports back file with results or results directly, main agent should make sure that it's clean and efficient before moving on to next task and does not pass any redundant context data to other subagents. Main agent stores as much informations as possible in files insted of keeping them in context all the time.
9. `src/config/openclaw.template.json` is updated accordignly if any changes specific to openclaw has been made.
9. Documentation in `docs` is updated accordingly after any changes are being made.

## Development rules and context
- use subagents to execute every step
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- always work on new feature branch when implementing multi step task based on plan. Commit often, but do not push anything to remote, let user to handle that after impleentation finishes
- use up to date documentation from this year. Check current date
- you're working with `openclaw version 2026.4.9`. Make any changes in agents definitions in a way it will work best in openclaw
- search for openclaw version multiagent best practices on the web before you'll jump into any implementation/modifications

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Agents are defined in `src/agents/`
- Openclaw documentation is available here: https://docs.openclaw.ai/