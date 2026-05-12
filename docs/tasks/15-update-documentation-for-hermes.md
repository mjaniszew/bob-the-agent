# Update documentation for hermes agent

Goal of this task is to update entire documentation inlcuding readme, architecture, installation etc. according to current state of project. Biggest change that has been made is that from now on agent uses Hermes Agent under the hood insread of Openclaw, and each specialized agent have it's own container where it's running

## Definition of Done
1. Documentation in `docs/*` is updated according to current state of project
2. Readme is updated with new information about agent architecture and how it works
3. Installation instructions are updated to reflect changes in projec
4. Architecture diagram is updated to reflect new architecture

## Additional context
- agent now uses Hermes Agent insted of Openclaw. In order to search for it's specifics lookup docummentation available here: https://hermes-agent.nousresearch.com/docs and here: https://github.com/NousResearch/hermes-agent/tree/main/website/docs
- compose.yaml now uses `dockerfiles/Dockerfile.hermes` as a base to build agent image
- entire architecture now relies on assumption that each agent has it's own container where it runs, and they communicate with each other using NATS. There are multiple containers: ollama (models provider), searxng with valkey(search engine used by agent to search web), agent-main (main orchestrator agent, which also provides Discord Bot and other main capabilities), researcher / simple-agent (agents that perform specific tasks delegated by main agent), NATS container (not implemented yet, but it will be soon, it's communication layer for main agent to delegate task to specialized agents, and lsiten for results)
- `src/scripts/*` has been modified in oreder to serve multiple Hermes Agents setup from now on
- `src/volumes` also now contains Hermes Agent specific data like workspace and configuration files, mounted directly to container
- `src/skills` have been reworked, analyze that too