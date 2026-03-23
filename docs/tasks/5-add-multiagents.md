# Add Multiagents

Add proper support for multiagents handlig with openclaw.

## Definition of Done
1. There's multiple agents configured in openclaw configuration, each for respective particular task:
- `main` - default multimodal agent which purpose is to handle basic tasks, and to delegade specialized work into sub agents. This agent should use `multimodal` model, and it should prepare plan for fulfilling task delegating it's parts into other agents. It should be able to spawn any other agents defined and enabled in openclaw
- `Web Searcher` - agent which purpose is to handle web search, using `web-search` skill and tool, as well as additional `grok-search` skill and it's tool, when search is specifically meant to be via Grok, or search relates to Twitter/x.com, it should also use built in openclaw `Web Serach` tool as a fallback
- `Data Extractor` - agent which purpose is to handle data extraction from websites and documents, using built-in `data_extraction` skill and connected tool, as well as `Web Fetch` tools when applicable eg. fetching particular links directly
- `Research and Analyze` - agent which purpose is to handle research tasks and analyze extracted data
- `Document Creator` - agent which purpose is to handle creating documents as a result of research conducted by other agents. It should use built-in `document_creation` skill and connected tool
2. Each agent have it's own basic workspace structure defined in `src/agents/{agent_name}`, as well as corresponding config entry in `openclaw.template.json` pointing to proper workspace in container. Workspace should consist of typical .md files eg. AGENTS.md  BOOTSTRAP.md  HEARTBEAT.md  IDENTITY.md  SOUL.md  TOOLS.md  USER.md and propper content inside those files, as per current openclaw docs.
3. There's additional step in Dockerfile.agent which transfers contents of `src/agents/{agent_name}` into app direcory in container `/app/workspace/{agent_name}`.
4. Each agent checks what date currently is and runs it's tasks based on that date eg. when searching for researces online, it should check what date it is and search making sure that it's not searching for data from future or past if search is about docummentations, news, or any other data with is time sensitive, inless it's specifically stated in task that it shoudl search for data from particular time.
5. Everythign is tested properly with actual docker run, and checked whether agents are working properly.


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
- Use up to date openclaw docuemntation fro this year, it's March 2026