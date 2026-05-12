# Rework Web Search

Goal of this task is to make sure agent will have searxng implemented and will be using it as a main search engine, no other web search enginges eg. through grok will be used. Also for x.com search there will be always grok-search tool used.

## Definition of Done
1. There's `searxng` implemented as a container added when calling `compose.yaml`
2. It's possible to apply initial configuration of searxng during `docker compose`
3. `web-search` skill is removed along with all it's refferences and documentation
4. `grok-search` skill uses `XAI_SEARCH_API_KEY` instead of default `XAI_API_KEY`:
 - value is updated in codebase and all related places
 - .env.template is updated accordignly with additional explaination that it's used for x.com search but it's value is different than default `XAI_API_KEY` in order to prevent openclaw to using it for other purposes like WebSearch tool, which would consume tokens. Instead we'll use searxng which does not consume tokens wehn searching
3. Skills are udapted to contain searxng skill if necessary
4. Documentation in `docs` is updated accordingly:
 - searxng part is added
 - everything related to other web-search or grok-search skills is updated

## Development rules and context
- Use subagents to execute every step. Agents need to save their ouptut results in `/app/data` and report output files back to main agent
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- always use TDD approach. Always write failing tests first, and then implemente and test, all accordign to prepared plan
- always work on new feature branch when implementing multi step task based on plan. Commit often, but do not push anything to remote, let user to handle that after impleentation finishes
- searxng need to have initial configuration performed eg. English language for search set, either directly or as openclaw documentation. Research best approach based on found resorces on the web
- research best approaches on the web to implement searxng in docker container, do not rely only on documentation
- use up to date documentation from this year. Check current date
- test in docker container, not in local environment

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Skills are defined in `src/skills/`
- searxng docs are located here: https://docs.searxng.org/
- openclaw searxng section in documentation is located here: https://docs.openclaw.ai/tools/searxng-search#searxng-search
