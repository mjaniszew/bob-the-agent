# Clean repository and update documentation

Clean repository by removing redundant pieces, and update documentation to reflect current state

## Definition of Done
1. `scheduling` skill is removed from the repository, along with all it's references, tests, openclaw config, and documentation
2. `notifications` skill is removed from the repository, along with all it's references, tests, openclaw config and documentation
3. `document-creation` skill is removed from the repository, along with all it's references, tests, openclaw config and documentation
4. Agents are updated to reflect changes in skills
5. Current codebase is analyzed and entire documentation is updated to reflect current state:
  - `src/docs` is updated
  - `README.md` is updated
6. Additional changes are made in `README.md`:
  - Acknowledgments section is removed
  - Web Dashboard informations are removed
  - Additional steps are added for Quick Start section: ollama signin command to be ran in ollama container on first run, and `openclaw pairing approve discord` command to be ran in agent container on first run

## Rules and context
- use subagents when feasible
- save detailed plan in `docs/plans` before executing any steps
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- always work on new feature branch when implementing multi step task based on plan. Commit often, but do not push anything to remote, let user to handle that after impleentation finishes
- use up to date documentation from this year. Check current date
- test in docker container, not in local environment

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Skills are defined in `src/skills/`
