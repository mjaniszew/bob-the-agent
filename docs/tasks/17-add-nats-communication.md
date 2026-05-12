# Add nats communication

Goal of this task is to implement proper nats communication between agent containers working in a way that main agent will be able to delegate tasks to specializet agents, and specialized agents are listening for new tasks, executing them and reporting back results.

## Definition of Done
1. There's new skill `agent-to-agent` created and available to be used by agents. This skill have following requirements:
- it can send new message to nats container with task details, which will be received by specialized agent who's listening on that message. Message should always contain id of agent who's sending it, and id of agent who should receive it. Only selected agent should be able to receive that message.
- it can check nats queues for new messages, and if there's any message for current agent, it should be executed
- it contains python script implementing required functionalities
2. There's python script `register-nats.py` which will be launched during container run and will register agent with nats server allowing for agent to receive tasks. This script have following requirements:
- script registers agent in nats under specific id, and listens for new messages meant for that agent
- when new message is received, it's executed by egent eg. using hermes cli. When agent finishes task execution it announces results using `agent-to-agent` skill sending result message to the same agent this message came from
- it should be located in `/app/scripts` within container
- it should be launched when container is being run eg. as a part of `/app/scripts/hermes-entrypoint.sh`
- sources should be placed under `/src/scripts/` and then copied to `/app/scripts` during build process
3. Skill as well as script are implemented according to nats documentation, using proper communication techniques (like subjects, queues, pub-sub, etc.).
4. All the changes are committed with proper commit messages.
5. Skill and script has tests written in TDD manner. Tests should be written before implementation based on prepared implementation plan, and fail, then after implementation they should pass. All tests should be done using actual docker containers
6. Documentation in `docs` is updated accordingly:
 - x-com part is added
 - `main` and `web-searcher` agents are updated

## Development rules and context
- use subagents to execute every step
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- always use TDD approach. Plan should start with failing tests first, and then implement functionality in a way it will make tests pass
- always work on new feature branch when implementing multi step task based on plan. Commit often, but do not push anything to remote, let user to handle that after impleentation finishes
- use up to date documentation from this year. Check current date
- test in docker container, not in local environment

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Skills are defined in `src/skills/`
- Use nats official documentation and ensure proper nats communication is established between agents, using sdks, and proper communication patterns according to official documentation (like subjects, queues, pub-sub, etc.)
- Use hermes agent offictial documentation for understanding how to lauch agent task when new message arrives for specific hermes agent
