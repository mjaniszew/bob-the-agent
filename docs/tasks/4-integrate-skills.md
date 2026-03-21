# Task: Integrate Skills with OpenClaw Agent

## Objective

Connect the existing TypeScript skill implementations in `src/skills/` with the OpenClaw agent so they can be invoked during task execution.

## Current State

- Skills exist: web-search, data-extraction, math-operations, document-creation, notifications, scheduling
- `src/skills/index.ts` exports `executeSkill()` function and skill registry
- Skills are NOT loaded by OpenClaw agent
- OpenClaw uses its own internal skill system
- The scheduling skill imports from `../api/src/database` but API runs in separate web container

## Required Work

### 1. Research OpenClaw Skill/Tool Integration API

Investigate how OpenClaw loads and uses skills:
- Check OpenClaw documentation for tool/skill integration
- Identify if skills should be native OpenClaw tools, external API calls, or embedded functions
- Understand the expected interface format

### 2. Determine Integration Approach

Options to evaluate:
- **Native OpenClaw tools** - If OpenClaw supports custom tool registration
- **External API calls** - Skills exposed as endpoints via web container
- **Embedded functions** - Skills compiled into agent container

### 3. Update Skill Implementations

Based on integration approach:
- Update skill interfaces to match OpenClaw's expected format
- Ensure skill parameters and return types align with OpenClaw tool interface
- Handle async execution patterns

### 4. Add Skill Registration at Startup

- Register all skills when agent starts
- Ensure skills are discoverable by OpenClaw's tool system
- Handle skill loading errors gracefully

### 5. Handle Scheduling Skill Database Dependency

The scheduling skill currently imports from `../api/src/database`:
- This won't work in agent container (API is in separate container)
- Need to either:
  - Move scheduling to web container
  - Create API endpoint for scheduling operations
  - Use shared database connection

### 6. Update Documentation

- Document integration approach in `docs/ARCHITECTURE.md`
- Update `docs/features/SKILLS.md` with integration details
- Add usage examples for each skill

## Dependencies

- Task 1 (initial agent) - Complete
- Docker setup working
- OpenClaw running in container

## Testing

1. Verify agent starts without errors
2. Test each skill individually through agent
3. Test skill combinations (e.g., web search → data extraction)
4. Verify error handling for invalid parameters
5. Test skill timeout behavior

## Files to Modify

| File | Change |
|------|--------|
| `src/skills/index.ts` | Add OpenClaw registration |
| `src/scripts/app-entrypoint.sh` | Add skill initialization if needed |
| `docs/ARCHITECTURE.md` | Document final integration approach |
| `docs/features/SKILLS.md` | Update integration status |