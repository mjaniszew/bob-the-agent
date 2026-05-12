# Implement x-com skill

Goal of this task is to implement x-com skill which will handle interactions with x.com api directly, instead of using Grok.

## Definition of Done
1. `x-com` skill is able to search recent and all posts from x.com platform using x-api directly, not using Grok.
2. `x-com` skill is able to search users and their timeline from x.com platform using x-api directly.
3. `x-com` skill uses `X_COM_API_TOKEN` instead of default `XAI_API_KEY`
4. Each search option can take query param to search, which includes phrase to search along with additional options like, time range, tags. It's implemented according x-api documentation.
5. Skill supports pagination in case of large number of results
6. Skill is implemented in `src/skills/x-com` directory in the same way and convention as other skills
6. Agents in `src/agents` are udapted to be able to use `x-com` skill. Agents who should be able to use `x-com` skill: `main`, `web-searcher`
7. Documentation in `docs` is updated accordingly:
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
- Use X-api directly, it's lokated here: https://docs.x.com/x-api/posts/search/introduction
- Do not implement search through Grok, this skill purpose is to be cost-effective and not to use Grok model in order to search
- Research best option to implement posts and users search accordign to documentation, be thorough
