# AGENTS.md

This file contains general instructions for all AI agents. Needs to be followed strictly.

## General
- never read, use or modify any `.env` and `/env.*` files
- use separate subagents when feasible eg. perform actions like reading files, web search, planning, and execution as separate subagents
- read `docs/OVERVIEW.md` before starting any work.
- do not read any files in docs unless explicitly asked to do so as a part of task, or stated in `docs/OVERVIEW.md`
- when task have multiple steps, always execute in plan mode, prepare plan first, and execute plan second instead of executing all steps directly from task definition. Decission whether it should be in plan mode or not is up to you your best judgment
- always use up to date informations and documentations when searching in web from current year, unless specifically asked otherwise, or given task requires archival data. It's year 2026 but alwyas double check

## Shell commands
- never ask whether to run shell command if it relates only to agent workspace. Just run them