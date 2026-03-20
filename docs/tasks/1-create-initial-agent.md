# Create Initial Agent

Create first fersion of agent which will be possible to be deployed as a container or multiple containers through Docker Compose.

## Definition of Done
1. Plan for development is prepared and saved as a plan file in `docs/plans`. Plan should cover these areas:
- all features defined in `docs/features`
- should follow requrements from `docs/OVERWIEV.md`
- should be as detailed as possible,
- should start with detailed list o all steps that will be executed, including test plans containing how to verify whether those steps are properly done and working
- each meaningfull feature or part of features should have separate steps in plan along with test plans
2. Agent is created and verified with test plans whether it works correctly.
3. All features defined in `docs/features` are implemented within agent, and tested with test plans.
3. Dockerfile and/or docker-compose.yaml is created and verified with test plans.


## Development Context
1. Use subagents to execute every step
2. Within every step, use subagents to execute specific actions like reading files, web search, planning, editing/creating files, and execution
3. Start with creating plan.
5. Before doing agent implementation, create Dockerfile and/or docker-compose.yaml first, and verify whether it works correctly.
6. Do agent implementation and testing using working container, do not run it outside container.
7. Execute each step in plan separately, implementing and testing whole thing each time, if it fails fix it before proceeding into next step
6. Always start with defining tests in way they should test particular feature/step, and verify whether it initially fails, then implement feature/step, and test again if it passes, if it still fails, fix and test again. Interate until particulare feature/step is resolved before moving into next one.
8. Before executing any step, verify whether it is correct.
9. If you'll keep failing with particular step repeaddly, and you'll decide it migh not be possible to implement, do not proceed into next one. Instead, fail and return error message to user with suggestion waht to change in task in order to be able to move forword with implementation next time eg. what informations / context is needed to be able to implement it.

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Agent General Requirements: `docs/features/GENERAL.md` - contains general requirements that agent should fulfull, and main features it should have. Read it second
- Agent Skills: `docs/features/SKILLS.md` - contains list of particular skills agent should have implemented along with purpose descriptions and requirements particular skill should fulfill in order to be considered as working
- Model Providders: `docs/features/PROVIDERS.md` - contains list of providers Agent should support in order to load different models for particular skill and features