# Create Grok Search Skill

Create skill along with all required code / app in order to enable openclaw to use Grok Search API for web and x.com platform searching.

## Definition of Done
1. Plan for development is prepared and saved as a plan file in `docs/plans`. Plan should cover these areas:
- web search implementation using Grok Search API according to web search features described in `docs/features/SKILLS.md`. It should cover acual openclaw skill implementation, along with proper config section in `openclaw.template.json`, and if necessary with additional application to be runned by skill in order to provide search results through Grok Search API, unless it's already achevable by some openclaw configuration, or plugin
- proper tests are prepared in order to test implementation functionality. Tests should fail before implementation, and pass after implementation of functionality
2. Tests are created according plan, and they fail untill Skill is fully implemented.
2. Skill is developed according the plan.
3. Implementation is tested with tests which should pass after implementation.

## Development Context
1. Use subagents to execute every step
2. Within every step, use subagents to execute specific actions like reading files, web search, planning, editing/creating files, and execution
3. Start with creating plan.
4. Do agent implementation and testing using working container, do not run it outside container.
5. Execute each step in plan separately, implementing and testing whole thing each time, if it fails fix it before proceeding into next step
6. Always start with defining tests in way they should test particular feature/step, and verify whether it initially fails, then implement feature/step, and test again if it passes, if it still fails, fix and test again. Interate until particulare feature/step is resolved before moving into next one.
7. Before executing any step, verify whether it is correct.
8. If you'll keep failing with particular step repeaddly, and you'll decide it migh not be possible to implement, do not proceed into next one. Instead, fail and return error message to user with suggestion waht to change in task in order to be able to move forword with implementation next time eg. what informations / context is needed to be able to implement it.

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Agent General Requirements: `docs/features/GENERAL.md` - contains general requirements that agent should fulfull, and main features it should have. Read it second
- Agent Skills: `docs/features/SKILLS.md` - contains list of particular skills agent should have implemented along with purpose descriptions and requirements particular skill should fulfill in order to be considered as working