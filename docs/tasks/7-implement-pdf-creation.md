# Implement PDF Creation

Research and implement working logic for `document-creation` skill. It should be able to create pdf files with proper structure

## Definition of Done
1. Implementation plan is created and saved in `docs/plans`. Plan follows these rules:
 - it describes pdf creation logic
 - it describes a way to test whether pdf is properly created and displayed
 - it forces execution in order: 1. Tests are created 2. Tests are failing 3. Logic is implemented 4. Tests are passing
 - plan and tests contain example tabes with example lorem ipsum data, testing cases with data overflowin from table cells, and very long paragraphs
2. Implementation plan is executed in proper order
3. `document-creation` skill when ivoked is able to properly create pdf file
4. Created pdf file has valid pdf structure and is properly displayed on devices. It sohuld properly display tables with no text overflowing outside of table cells.
5. Verification tests are passing and verifying that pdf is properly displayed without any text overflowing outside of table cells
6. `document-creation` skill along any archidecture doc files in `docs` and agent files in `src/agents` are updated and reflect new implementation
7. `src/skills/index.ts` is up to date with new implementation

## Development rules and context
1. Use subagents to execute every step. Should agents should save their results in `/app/data` and report files back to main agent
2. Verification tests should use playwright for visual verification
3. For pdf creation research and choose proper npm package. Check github for examples of usage and for best opinions with higher amount of starts and less amount of issues. As a part of research, validate pdfkit as viable solution.
4. Validate whether logic works, use playwright to verify visual results. If creation fails or pdf is incorrectly displayed, fix and repeat unit all issues will be resolved.
5. Do not launch and test outside docker containers. This environment has docker installed, use it to lauch within container and test.
6. Search internet only with up to date documentations from this year.

## Additional resources and help to fetch and use
- Agent Overview: `docs/OVERVIEW.md` - contains general information about agent, its purpose, and main assumptions. Read this first
- Agent Skills: `docs/features/TOOLS.md` - contains list of particular tools agent should have implemented along with purpose descriptions and requirements particular tool should fulfill in order to be considered as working. Focus on `Agent Browser` tool
- `document-creation` skill is defined in `src/skills/document-creation`