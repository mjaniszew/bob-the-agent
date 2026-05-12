# Implement grok-search skill

Goal of this task is to implement grok-search skill which will handle searching x.com/twitter through grok x.ai api directly if x-com skill is failing, or if requested specifically in prompt.

## Definition of Done
1. `grok-search` skill is able to search recent and all posts from x.com platform using Grok models over x.ai api.
2. `grok-search` skill is able to search users and their timeline from x.com platform using Grok models over x.ai api.
3. `grok-search` skill uses `XAI_SEARCH_API_KEY` instead of default `XAI_API_KEY` in order to prevent openclaw to automatically use it for web search.
4. Implementation using Grok Search API according to web search features described in `docs/features/SKILLS.md`. It should cover acual openclaw skill implementation, along with proper config section in `openclaw.template.json`, and if necessary with additional application to be runned by skill in order to provide search results through Grok Search API.
5. Skill is implemented in `src/skills/grok-search` directory in the same way and convention as other skills
6. Agents in `src/agents` are udapted to be able to use `grok-search` skill. Agents who should be able to use `grok-search` skill: `main`, `web-searcher`
7. Tests are created first, and they fail untill Skill is fully implemented. Tests should test api calls and their responses, as well as test whether agent is able to use skill in actual Docker enviroment using Docker Compose.
8. Implementation is tested with tests which should pass after implementation.
9. Documentation in `docs` is updated accordingly.

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
- Use X-search api through available SDK if possible, documentation is located here: https://docs.x.ai/developers/tools/x-search
- Find most cost-effective way of using Grok modesl for searching x.com/twittetr. Spend some time researching available documentation and choosing most cost-effective way and grok model
- Research best option to implement posts and users search accordign to documentation, be thorough
