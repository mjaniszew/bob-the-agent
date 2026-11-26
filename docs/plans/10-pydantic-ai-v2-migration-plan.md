# Bob-harness: Pydantic AI 2.x Migration — Implementation Plan

Task: docs/tasks/10-pydantic-ai-v2-migration.md · Repo: /opt/data/projects/bob-the-agent · Branch: custom-harness-poc · Target: pydantic-ai==2.73.0 + pydantic-ai-harness[coder]==0.73.0
Status: **PLAN ONLY — NO IMPLEMENTATION** (this document is the deliverable; implementation phases are executed later via the TDD phases in Part 2). Authored via OpenCode plan agents against live-probed 2.73.0/0.73.0 surfaces (fact sheet: verified API probes + repo survey, 2026-11-24).

Contents:
- Part 1: §0 Executive summary · §1 Scope/non-goals · §2 Design decisions D1–D7 · §3 Target architecture + config additions
- Part 2: §4 Phase-by-phase TDD plan (P0–P9) · §5 Verified risk checklist (18 rows) · §6 Compatibility matrix (31 rows)
- Part 3: §7 Open questions OQ1–OQ8 with decided defaults · §8 State snapshot procedure (STATUS.md) · §9 Execution protocol, review, proof of work · Appendix A command crib · Appendix B resume template

Binding convention: D1–D7, OQ1–OQ8 and the P0–P9 contracts are binding for the implementation agent; overrides are recorded in STATUS.md (§8.1).

---

## Part 1 — Design decisions, target architecture and configuration

Target doc: `docs/plans/10-pydantic-ai-v2-migration-plan.md` · Repo: `/opt/data/projects/bob-the-agent` · Branch: `custom-harness-poc` (HEAD 7200ad3)
Plan-Part scope verified against: live probes of pydantic-ai 2.73.0 + pydantic-ai-harness[coder] 0.73.0 (python 3.13 venv) and a grep-level survey of `src/bob-harness/bob_harness/` (10,294 LOC, 24 modules; 31 test files, 11,534 test LOC).

## 0. Executive summary

- **What migrates:** the single pydantic-ai import surface today is `router.py` (980 LOC); it converts param-for-param to v2 `Agent` (all ctor params the router uses still exist in 2.73.0 — verified), and the executors move to the v2 Agent + capability model via `pydantic-ai-harness` 0.73.0.
- **Pin bump:** `requirements.lock` (frozen R1) is regenerated ONCE, at the dedicated dependency phase: `pydantic-ai==2.73.0`, `pydantic-ai-harness[coder]==0.73.0`; `requirements.txt` flips the DELIBERATE `>=1,<2` cap with a new rationale comment (the v1 "schema-retry break" concern is superseded by the deferred-tool redesign, D3).
- **What is preserved byte-identically:** router schema-enforced `route`/`analyze` outputs, keyword fallback tables, `router_ledger.jsonl` behavior (`router.py` D7); hermes/opencode subprocess adapters (argv snapshot contracts, `--resume`/`--session`, `build_child_env` `BOB_HARNESS_*` allowlist); task FSM + `tasks.py` event registry; `gates.jsonl` + 24h gate timer; Subtask drop (SUBTASK_DROP) chaining + sole-invoker invariant; gateway port 8181/API surface, Discord home-channel/threads behavior; APScheduler job kinds.
- **Executor architecture:** a new `executor` adapter implementing the existing `AgentRun` protocol wraps the v2 pydantic agent + harness capabilities, so `taskloop` remains the single task runner and every consumer keeps the `StreamEvent` contract (D2).
- **Hermes/OpenCode as subagents:** mapped via a custom `delegate_run` capability (toolset), NOT harness `SubAgents` entries — `SubAgent.agent` requires an in-process `AbstractAgent` message graph, which would fake the CLIs' protocol state (D1).
- **WAITING_INPUT gate:** rebuilt on the v2 deferred-tool mechanism (verified end-to-end: `CallDeferred` → `DeferredToolRequests` → resume with `DeferredToolResults`) plus the `AskUser` capability; parked questions survive restarts because `message_history` + results seed are persisted in the task folder (D3); FSM/API unchanged (D3).
- **Memory/skills:** one shared `FileMemoryStore` rooted at `<data_dir>/memory` (verified: writes plain `MEMORY.md`-style files, no sidecars) keeps the budgeted plain-file format intact; `Skills` capability mounts the existing `SKILL.md` folders while `SkillsEngine` keeps owning the byte-deterministic `INDEX.md` (D4, D5).
- **What is NOT adopted:** harness `Coder`/`FileSystem`/`Shell`/`DockerContainer` and `ModelRouter` capabilities — OpenCode stays the coder via delegation; workspaces only supply `LocalWorkspaceBackend` for the memory/skills stores (D6, D7). The `[coder]` extra is still installed because `json-repair` is a hard import requirement of the package (verified ImportError).
- **Risk posture: LOW–MEDIUM.** API-compat risk is largely retired by live probes (ctor params, `run`/`run_sync` signatures, structured output via `run.output`, exception imports, deferred round-trip all verified on 2.73.0); residual risk concentrates in executor behavior parity (tests) and config/serialization plumbing (Part 2 risk checklist greps each symbol per phase).
- **Delivery:** TDD-first phases (failing tests → implement → green → commit), state snapshot at `/opt/data/results/2026-11-24/pai2-migration/STATUS.md` after every phase, no docker-in-docker (fake/mock capability unit tests + existing `FakeAdapter` seam + updated `host-smore`-style smoke scripts) — phase breakdown, LOC estimates, risk checklist and compatibility matrix follow in Part 2.

## 1. Scope, non-goals and governing documents

### 1.1 Scope

| Area | From → To |
|---|---|
| pydantic-ai | 1.107.10 (frozen R1) → **2.73.0** pinned in `requirements.lock` (incl. `-slim`/`-graph`/`-evals` 2.x line) |
| harness SDK | (absent) → **pydantic-ai-harness[coder]==0.73.0** pinned; container build installs the extra (json-repair; pulls pillow+ripgrep) |
| `router.py` | v1 `Agent` ×2 lazy + `run_sync`/`run` → v2 `Agent`, same models, same fallback/ledger contract (D7) |
| Executors | subprocess-only dispatch → adapters registry **gains `executor`**: v2 `Agent` + harness capabilities (`Memory`, `Skills`, `AskUser`, nested `SubAgents`, custom `delegate_run`) (D1, D2) |
| Gate | prompt-append WAITING_INPUT park → deferred-tool `DeferredToolRequests`/`DeferredToolResults` flow with persisted history seed (D3) |
| `memory.py`/`skills.py` | reimplemented over `FileMemoryStore`/`Skills` where they interoperate with the executor; plain-file format + budgets + INDEX.md contract preserved (D4, D5) |
| Config | `config.py` (673 LOC, `extra='forbid'`, flat `BOB_HARNESS_*` env map) gains a `harness:` sub-model (§3.4) |
| Docs/tests/docs-in-repo | OVERVIEW/ARCHITECTURE/CONFIGURATION v2 sections; screenshot-ready gateway e2e; `scripts/host-smoke.sh` + `scripts/dev-incontainer-tests.sh` updated |
| `PYDANTIC_AI_NO_BANNER` | set to `1` in image env (v2 prints an import banner — verified) |

Branch workflow per task rules: TDD phases, commit often (co-authored with mjaniszew, NEVER push), code review via opencode reviewer (max 3 fix rounds), 20–30 min scheduled checks with token resets every 5h.

### 1.2 Non-goals

1. **No SubAgents self-delegation replacing the real adapters.** hermes (subprocess, profiles `researcher`/`simple`) and opencode (subprocess) remain real subprocess adapters speaking their native CLI protocols (D1).
2. **No harness coder stack adoption:** `Coder`, `FileSystem`, `Shell`, `DockerContainer` capabilities are not mounted on any agent (D6); no docker socket in the container.
3. **No behavior change on the gateway/Discord surface:** port 8181, `/api/*` shapes (incl. `/api/tasks/{id}/answer` semantics), SSE chat streaming, session threads, home channel — updates touch the calls to the v2 agent surface only (requirement 6).
4. **No task-model/FSM changes:** `task.yaml` schema, `state.json` transitions (incl. `RUNNING ↔ WAITING_INPUT`), `events.jsonl` closed registry, SUBTASK_DROP sole-invoker invariant stay exactly as-is (requirement 7).
5. **No scheduler rework:** APScheduler wiring, 3 job kinds (`spawn_agent`, `run_script`, `drop_task`), misfire policy, runs ledger untouched; task spawns keep flowing through the same taskloop (requirement 8).
6. **No memory/skills format migration:** `MEMORY.md`/`USER.md`/`lessons.md` char budgets and existing skill folders remain the source of truth (no new layout ⇒ no migration step) (D4, D5).
7. **No child-env policy change:** `build_child_env` strict allowlist and the `BOB_HARNESS_*` deny prefix are untouched.
8. **No test infrastructure revolution:** no docker-in-docker; fakes/mock harness capability unit tests + existing `FakeAdapter`/router model-object seams (requirement 10).

### 1.3 Governing documents

- `docs/tasks/10-pydantic-ai-v2-migration.md` — task contract (DoD, dev rules, proof-of-work).
- `AGENTS.md` — repo-wide agent rules (read `docs/OVERVIEW.md` first; never touch `.env`/`env.*`; plan mode for multi-step work; up-to-date docs for web lookups).
- `docs/OVERVIEW.md` — project purpose and development workflow (mandatory reading before implementation).
- `docs/ARCHITECTURE.md` + `docs/DECISIONS.md` — referenced BY the modules being migrated and preserved through it:
  - **D2** (temperature-0 schema enforcement, one retry, deterministic keyword fallback, never-blocking) and **D16** (`analyze()` ledger `kind`) — `router.py:4-12`;
  - **D17** (SUBTASK_DROP chaining) and **F17** (boot replay: INTERRUPTED recovery, gate-restored timers) — `taskloop.py`;
  - **R3** (skills INDEX.md seeding — supervisor seeds `'# Skill index (generated by harness/skills.py at R3)'`) and **R5** (health-check placement, prompt-char cap, `_PROMPT_CHAR_CAP` budget row 11 `2200*4+margin`, ledger audit rows) — `router.py:8-14, 37-43`;
  - Plan section 7.3/m4b fallback banner contract (`data={"note": "routed by fallback (keyword table)"}`).
- Fact sheet of live probes (`/tmp/pai2-plan-context.md`) — this plan's verified-API substrate (2.73.0 / 0.73.0, python 3.13).

## 2. Design decisions D1–D7

### D1 — Delegation via a CUSTOM capability (toolset), NOT SubAgents entries

**Decision.** Exposure of hermes/opencode to the executor agent is implemented as a custom `AbstractCapability` exposing `delegate_run(...)` tools (via `get_toolset`), wrapping the existing adapters. hermes/opencode are NOT registered as harness `SubAgents(...)` entries.

**Rationale.** The 0.73.0 probe shows `SubAgent(agent=...)` must receive a pydantic-ai `AbstractAgent` — an in-process message-graph runner. The hermes/opencode adapters are external CLI subprocesses with their own protocol state (`hermes -p <profile> chat --oneshot --query-file …` argv snapshot contract, `--resume` / opencode `--session`/`--continue`), a strict child-env allowlist (`build_child_env` drops/asserts no `BOB_HARNESS_*`), and own `StreamEvent` streams that land in Session transcripts. Shimming them behind `AbstractAgent` would fake message semantics for no benefit and break the argv-snapshot/ledger/child-env tests. A custom capability that returns `delegate_run` tools keeps the adapters byte-for-byte as-is and satisfies the task's "map them as custom capabilities/tools the model calls" option.

**Alternatives considered, rejected.**
- *SubAgents entries with an adapter-shim AbstractAgent:* rejected — lies about message history, breaks `--resume`/`--session` resume contracts and snapshot fixtures, drags in `forward_deferred_calls`/`deferred_state_store` machinery that cannot cross a process boundary meaningfully.
- *Executor has no delegation at all (taskloop dispatches everything):* rejected — the executor must consult harness memory before delegating (task requirement) and must keep a delegation answer in the same run/turn (e.g., mid-run questions, D3).

### D2 — `executor` adapter in the existing registry; taskloop remains sole invoker

**Decision.** The v2 pydantic agent + capabilities are wrapped in an `executor` adapter implementing `AgentRun` (`run(spec) -> async iterator of StreamEvent`) and registered in the existing adapters registry alongside `hermes`/`opencode`/`fake`. `taskloop` stays the single task runner; delegation performed inside executor runs is nested, never task-origin; SUBTASK_DROP remains a taskloop/FSM-side event.

**Rationale.** `taskloop.py` (1529 LOC) already owns the inbox watcher, single runner queue (`tasks.max_concurrent` default 1), gate engine, SUBTASK_DROP chaining (D17) and boot replay (F17); `scheduler` drops tasks into the folder queue. Making the executor an adapter reuses the closed `StreamEvent` kinds (`started/chunk/final/tool/failed/system`), so gateway `_run_agent`, Session transcripts and the FakeAdapter/test seams keep working unchanged, and the task's "folder queue + SUBTASK_DROP sole-invoker invariant EXACTLY (harness-only dispatcher)" requirement is met literally. Nesting (in-process) keeps delegated work outside the task registry, honoring D17.

**Alternatives considered, rejected.**
- *Direct pydantic-agent call inside taskloop, bypassing the registry:* loses StreamEvent uniformity and the adapter-level test seams.
- *Executor emitting SUBTASK_DROP / spawning inbox tasks itself:* violates the sole-invoker invariant (D17) — rejected.

### D3 — WAITING_INPUT gate on deferred tool calls; persisted resume seed

**Decision.** When the executor needs user input, its ask tool raises `CallDeferred(...)` (AskUser capability) so `agent.run` returns `DeferredToolRequests`; TaskLoop parks the task exactly as today (state `RUNNING → WAITING_INPUT`, `gates.jsonl` row, `tasks.gate_timeout_s` default 24h, one re-notify then `FAILED(gate_timeout)`) and additionally persists `run.all_messages_json()` plus a `DeferredToolResults` seed into the task folder. `answer()` (via `tasks.py` WAITING_INPUT + `/api/tasks/{id}/answer`) rebuilds the executor agent and resumes with `deferred_tool_results=calls={tool_call_id: value}`; the '## User answer' append remains part of the transcript semantics. Subprocess adapters keep the existing prompt-append resume untouched.

**Rationale.** The deferred round-trip was verified end-to-end on 2.73.0 (`CallDeferred(kind='question'|'external')` → `DeferredToolRequests(calls, approvals, metadata, validated_args)` → resume completes); `DeferredToolResults(calls, approvals, metadata, answered_by)` is sufficient to finish the parked run. Persisting `all_messages_json` makes the parked question survive process restarts (requirement 5) — boot replay (F17) already restores gate rows and re-arms timers, and now also restores the message seed. Keep the FSM/gates.jsonl/timer/API identical so the closed events registry and UI/Discord consumers are unaffected.

**Alternatives considered, rejected.**
- *Inline blocking `Answerer` waiting for the answer:* breaks 24/7 autonomy and restart survival.
- *Executor park via prompt append + cold re-run from scratch:* loses in-run message history (token cost, semantics change), diverges from the v2 deferred-tool mechanism the task asks for.

### D4 — One shared memory store; plain-file format + budgets preserved

**Decision.** `MemoryEngine` is reimplemented over `FileMemoryStore(dir=<data_dir>/memory, workspace=LocalWorkspaceBackend(...))` so the harness `Memory` capability and the engine share ONE store; the existing `MEMORY.md`/`USER.md`/`lessons.md` plain-file format and `cfg.memory.budgets` (memory_chars 2200, user_chars, lessons_chars) are preserved; the executor's harness `Memory` capability auto-injects the digest, while delegated (subprocess) runs get read-only injection exactly as today.

**Rationale.** Live probe: `FileMemoryStore` writes plain files directly INSIDE the dir (`'<dir>/MEMORY.md'`; no sidecar dirs, no extra files), so the on-disk format and the existing budget/audit tests survive; `store.read(path, max_chars)` and `store.write` (`MemoryMutation(version=<sha256>, replayed, existed)`) replace the hand-rolled `_atomic_write_text`/version bookkeeping with SDK semantics while `MEMORY_EDIT` fenced-block rules and the `memory_audit.jsonl` audit stay engine-side harness policy. Sharing one store eliminates drift between the capability-injected context and the router digest (`digest()` ≤ 600 chars, unchanged).

**Alternatives considered, rejected.**
- *Migrate to a harness-native memory layout:* requires data migration for zero functional gain; breaks budget-contract tests.
- *Two stores (engine + capability with separate dirs):* double source of truth, digest/index divergence.

### D5 — Harness `Skills` capability on existing folders; `SkillsEngine` keeps INDEX.md

**Decision.** The `Skills` capability mounts the existing `<data_dir>/skills/*/SKILL.md` directories (`missing_directories='skip'`); `SkillsEngine` remains in the codebase and keeps sole ownership of the byte-deterministic `INDEX.md` generation (budget `cfg.memory.budgets.index_chars` = 1500, honest `ValueError`, atomic rewrite only when bytes differ) plus `capture()` (SKILL_DRAFT, slug `-2/-3` suffixes, `skill.captured` bus event). The supervisor's R3 seed line stays.

**Rationale.** The probe verified `Skills` scans `name`/`description` frontmatter — exactly the current folder contract — and exposes `get_toolset`/listen hooks for the executor; but `Skills` has no byte-deterministic index writer and the INDEX.md format is pinned by tests and the R3 seed, so letting the SDK own the index file would break the contract. `missing_directories` accepts `'error'`/`'skip'` only (`'ignore'` raises `ValueError` — verified), so boot on a fresh data tree uses `'skip'`. Skill bodies keep flowing through the existing hard-truncate-4000+marker path.

**Alternatives considered, rejected.**
- *Let harness `Skills` own an index file:* breaks byte-determinism tests and the seeded format.
- *`Skills` with `read_files=True` always-on:* unnecessary tool surface; body access stays explicit.

### D6 — Harness coder capabilities NOT adopted

**Decision.** `Coder`, `FileSystem`, `Shell`, `DockerContainer` capabilities (and `ModelRouter`) are not mounted on any agent. OpenCode stays the coder via delegation (D1). The only workspace machinery adopted is `LocalWorkspaceBackend` from `pydantic_ai.workspaces`, used to satisfy the `FileMemoryStore`/`Skills` store bindings. `pydantic-ai-harness[coder]` is still pinned because the package hard-requires `json-repair` at import time (verified: `ImportError: json-repair is required for RepairToolArguments` without the extra).

**Rationale.** Task DoD #2 defines Hermes and OpenCode as the delegated workers; a second in-process coding surface (file tools + shell with allow/deny lists + docker exec) would duplicate opencode's role, widen the security surface, and push beyond "behavior preserved" scope. The `[coder]` extra costs pillow+ripgrep in the image — acceptable and required for the pin to resolve.

**Alternatives considered, rejected.**
- *Adopt `Coder(autonomy='high', sub_agents=True)` for the executor:* duplicates opencode, changes proof-of-work surface, adds RepairToolArguments behavior differences.
- *Adopt `DockerContainer`:* needs docker access inside the container (docker-in-docker is explicitly out of scope, requirement 10).

### D7 — Router stays capability-free

**Decision.** `route`/`analyze`/`health_check` become v2 `Agent`s with NO capabilities — pure schema calls (`output_type=Route/Analysis`, `ModelSettings(temperature=0)`, `retries` unchanged), keyword fallback + `router_ledger.jsonl` behavior identical.

**Rationale.** The probe verified param-for-param compat: every ctor param the router uses (`model, name, output_type, model_settings, retries`) and every consumed surface (`run_sync/run`, `run.output` isinstance-verified, `run.usage.requests`, `all_messages()`, exceptions `UnexpectedModelBehavior/ModelHTTPError/ModelAPIError`, `RetryPromptPart` init signature) exists in 2.73.0, making this a mechanical port at constant risk. Capabilities would inject instructions/toolsets and demand workspace bindings, polluting the `_PROMPT_CHAR_CAP` budget (test-plan row 11 asserts `2200*4+margin`) and the manually-projected 600-char memory digests; the existing mock seam (model object injected via `model=...`) already covers all router tests, and `ModelRouter` — the harness's only router-named capability — was probe-verified to be a model-SELECTION capability, unrelated to our semantic routing role.

**Alternatives considered, rejected.**
- *Mount `Memory` on the router for auto-injection:* digests are deliberately hand-capped; auto-injection would break budget tests and fallback latency expectations.
- *Use `ModelRouter` for model choice:* wrong role (model selection, not turn routing).

## 3. Target architecture

### 3.1 Component diagram

                       ┌──────────────────────────────────────────────────────────────────────────────┐
  Web UI (HTTP 8181)   │  gateway/app.py + ui.py (FastAPI; /api/chat(+SSE), /api/tasks,                │
 ┌───────────────┐     │  /api/tasks/{id}/answer, /api/schedule, /api/handoff; API-key auth)           │
 │    browser    ├────►│  gateway/discord_bot.py (single identity, home channel, thread/session)       │
 └───────────────┘     └───────────────────┬──────────────────────────────────────────────────────────┘
 ┌───────────────┐  Discord DM/threads     │ user turn / task answer
 │ discord bot   ├────────────────────────►│
 └───────────────┘                         ▼
                       ┌──────────────────────────────────────────────────────────────────────────────┐
                       │ RouterBrain (v2, capability-free — D7)                                        │
                       │   route() ── Agent(output_type=Route, temp=0) ──┐  keyword fallback          │
                       │   analyze() ─ Agent(output_type=Analysis, ...) ─┤  router_ledger.jsonl       │
                       │   health_check() ─ schema ping ─────────────────┘  byte-identical          │
                       └───────────────┬──────────────────────────────────────────────────────────────┘
                                       │ adapter choice (chat → hermes/opencode; task → executor)
                                       ▼
 ┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ adapters registry (existing contract: run(spec) → StreamEvent{started|chunk|final|tool|failed|system}) │
 │                                                                                                   │
 │  hermes    (real subprocess CLI, argv snapshots, --resume, profiles)  ── UNCHANGED (D1)           │
 │  opencode  (real subprocess CLI, --session/--continue, workdir guard) ── UNCHANGED (D1)           │
 │  fake      (scenario-driven test seam)                                ── UNCHANGED                │
 │  executor  NEW (D2): AgentRun impl wrapping the v2 pydantic agent     ── capabilities below       │
 │                                                                                                   │
 │   ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
 │   │  pydantic_ai v2 Agent (executor) + harness capabilities                                   │  │
 │   │    Memory(store=FileMemoryStore(<data_dir>/memory, workspace=Local)) ── D4 shared store   │  │
 │   │    Skills(directories=[<data_dir>/skills/*], missing='skip')           ── D5              │  │
 │   │    AskUser(answerer=<wired>, id='ask_user')                            ── D3 deferred     │  │
 │   │    SubAgents(nested, in-process AbstractAgent only)                    ── capped, no CLIs │  │
 │   │    delegate_run  CUSTOM capability (D1) ────────────────────┐                            │  │
 │   └─────────────────────────────────────────────────────────────┼───────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────┼───────────────────────────────┘
                                       ▲                           │ nested delegate_run(hermes/opencode)
                                       │ (native CLI run, same     ▼ (StreamEvents merge into run;
                ┌──────────────────────┴───────────┐                adapters remain sole subprocess owners — D1/D2)
                │ bob_harness supervisor           │
                ▼                                  │
 ┌───────────────────────────────────────┐  ┌──────┴───────────────────────────────────────────────┐
 │ taskloop (SOLE task runner)           │  │ scheduler (APScheduler, unchanged kinds)             │
 │  inbox watcher → single runner queue  │  │  spawn_agent | run_script | drop_task ──► tasks inbox│
 │  gates: WAITING_INPUT (deferred D3:   │  └──────────────────────────────────────────────────────┘
 │    park + persist all_messages_json + │
 │    DeferredToolResults seed in task   │
 │    folder; answer() rebuild+resume)   │
 │  SUBTASK_DROP chaining (D17), boot    │
 │  replay (F17); CapabilityEvent bus    │
 │  topics considered (ask_user events)  │
 └───────────────────────────────────────┘

Event/bus: the existing `EventBus` (topics) keeps carrying `memory.updated`, `skill.captured`, `task.*`, `gate.*`; executor capability events (`AskUserRequestedEvent`/`AskUserAnsweredEvent`, verified built on `CapabilityEvent(kind, capability_id, tool_call_id, tool_name)` via `ctx.emit`) are consumed by the taskloop sink for the gate flow (requirement 7's "consider CapabilityEvent bus topics").

### 3.2 Capability manifest — executor agent

| Capability | Constructor wiring (verified 0.73.0) | Mount / data binding | Config keys (§3.4) | Notes |
|---|---|---|---|---|
| `Memory` | `Memory(store=FileMemoryStore(dir, workspace=LocalWorkspaceBackend(run_workdir)), agent_name='executor', namespace='', inject_memory=True, max_tokens=2000)` | `dir` = `harness.memory.dir` → `<storage.data_dir>/memory` (SHARED with `MemoryEngine`, D4 — probe: plain `MEMORY.md` written inside dir, no sidecars) | `harness.memory.*` | Gives the executor auto-injected memory per run; `MemoryEngine` keeps fenced-edit/audit/digest semantics on the same store; delegates get read-only injection as today |
| `Skills` | `Skills(directories=[<data_dir>/skills], missing_directories='skip', duplicate_names='error', unknown_names='error', read_files=False)` | existing `SKILL.md` folders; `INDEX.md` stays `SkillsEngine`-owned (D5) | `harness.skills.*` | `include`/`exclude` slugs optionally filter mounted skills; toolset exposes skill lookup to the executor |
| `AskUser` | `AskUser(answerer=<gateway/discord answerer or None>, timeout=None, id='ask_user')` | tool name `ask_user_question`; Questions 2–6 options; events via `ctx.emit(CapabilityEvent)` → taskloop sink | `harness.ask_user.*` | In task context the run returns `DeferredToolRequests` (D3); restart survival via persisted message seed |
| `SubAgents` (nested only) | `SubAgents(agents=[SubAgent(agent=<internal executor-style AbstractAgent>, name='nested_helper', description=…, can_ask_questions=True, read_only=True)], max_depth=3, forward_deferred_calls=False)` | in-process only; NEVER wraps hermes/opencode (D1) | `harness.subagents.*` | Provides decompose/verify-style nested runs; read-only default keeps nested arms from writing memory/skills out of policy |
| `delegate_run` (custom) | custom `AbstractCapability`: `get_toolset` → `delegate_run(agent: 'hermes'\|'opencode', prompt, profile?, workdir?, resume_ref?)` impl calls the registered adapter's `run(spec)` and re-streams `StreamEvent`s into the executor run (`wrap_run_event_stream`) | adapters registry (same instances the gateway uses) | `harness.delegation.*` | The mechanism by which "harness uses Hermes and OpenCode as subagents for delegated work" (task DoD #2) holds without `SubAgents` entries; child-env rules untouched |

Explicitly NOT mounted (D6): `Coder`, `FileSystem`, `Shell`, `DockerContainer`, `ModelRouter`, harness `MemoryRouter`-style extras. The executor's `output` is the structured run result streamed to the session/task transcript via existing `StreamEvent(final|chunk)` kinds; structured `output_type` verified working on 2.73.0 (`run.output` isinstance-validated).

### 3.3 What stays capability-free and why

- **Router (`router.py` route/analyze/health_check, D7):** pure schema calls with temperature-0, one retry, and the keyword fallback as the decision floor. Capabilities would (a) inject instructions/toolsets that break the `_PROMPT_CHAR_CAP` budget (test row 11 asserts `2200*4+margin`), (b) require workspace bindings that add a failure mode to a component whose contract is "never-blocking" (DECISIONS.md D2), and (c) change ledger/fallback bytes. The router also never touches `<data_dir>/memory` or skills directly — it consumes pre-capped projections (`digest()` ≤ 600 chars ×2, index ≤ 1500 chars, last-3 transcript events), so there is nothing for capabilities to inject.
- **No `ModelRouter`:** probe-verified to be a model-selection capability, not a routing-role capability; adopting it would be a misread of the harness vocab.
- **Subprocess adapters:** remain capability-irrelevant — they are protocol implementations, not agents.
- Banner hygiene: every v2 import path sets `PYDANTIC_AI_NO_BANNER=1` in the image so supervisor logs and the smoke scripts stay identical in shape.

### 3.4 Configuration additions

Mechanics (must-hold): `HarnessConfig` has `extra='forbid'` and a flat `BOB_HARNESS_*` env map (`config.py:192-199`) bound via `_FlatEnvSource`; nested-delimiter env names are NOT read at unknown paths — every new flat env var must be added to `_FLAT_ENV_MAP`, and every new YAML key must land in a new `harness:` sub-model of `HarnessConfig`. Precedent: list-valued keys are YAML-only with no flat env binding (comment at config.py:445); `BOB_HARNESS_WORKDIR` proves short aliases for deep paths are fine.

New `harness:` group (defaults shown; YAML-first, flat envs optional):

```yaml
harness:
  memory:
    enabled: true          # Memory capability on the executor
    dir: ''                # '' -> <storage.data_dir>/memory ; shared with MemoryEngine (D4)
    workspace_root: ''     # '' -> per-run workspace (task workdir / session workdir) ; LocalWorkspaceBackend binding
    agent_name: executor   # Memory(store=..., agent_name=...)
    inject: true           # inject_memory=True/False
    max_tokens: 2000       # Memory max_tokens (SDK default, verified)
  skills:
    enabled: true          # Skills capability on the executor
    include: []            # optional slug filter (Skills include=)        [YAML-only, list-valued]
    exclude: []            # optional slug filter (Skills exclude=)        [YAML-only, list-valued]
    read_files: false      # Skills read_files
    missing_directories: skip  # 'error' | 'skip' (verified: 'ignore' is a ValueError)
  ask_user:
    enabled: true          # AskUser capability on the executor
    id: ask_user           # AskUser(id=...) ; tool name ask_user_question
    answerer: deferred     # 'deferred' (D3 default: run parks via DeferredToolRequests;
                           #  TaskLoop answers on resume) | 'inline' (interactive
                           #  Answerer + timeout below) | 'none'
    timeout_s: null        # AskUser(timeout=...) when answerer=inline
  subagents:
    enabled: true          # nested SubAgents capability
    max_depth: 3
  delegation:
    enabled: true          # delegate_run custom capability (D1)
    tool_name: delegate_run  # [YAML-only]
```

Flat env bindings to add to `_FLAT_ENV_MAP` (following the existing `BOB_HARNESS_*` pattern):

| Config key | Env var | Default |
|---|---|---|
| `harness.memory.enabled` | `BOB_HARNESS_MEMORY_ENABLED` | `true` |
| `harness.memory.dir` | `BOB_HARNESS_MEMORY_DIR` | `''` → `<storage.data_dir>/memory` |
| `harness.memory.workspace_root` | `BOB_HARNESS_MEMORY_WORKSPACE` | `''` → per-run workdir |
| `harness.memory.agent_name` | `BOB_HARNESS_MEMORY_AGENT_NAME` | `executor` |
| `harness.memory.inject` | `BOB_HARNESS_MEMORY_INJECT` | `true` |
| `harness.memory.max_tokens` | `BOB_HARNESS_MEMORY_MAX_TOKENS` | `2000` |
| `harness.skills.enabled` | `BOB_HARNESS_SKILLS_ENABLED` | `true` |
| `harness.skills.read_files` | `BOB_HARNESS_SKILLS_READ_FILES` | `false` |
| `harness.skills.missing_directories` | `BOB_HARNESS_SKILLS_MISSING` | `skip` |
| `harness.ask_user.enabled` | `BOB_HARNESS_ASK_USER_ENABLED` | `true` |
| `harness.ask_user.answerer` | `BOB_HARNESS_ASK_USER_ANSWERER` | `deferred` |
| `harness.ask_user.timeout_s` | `BOB_HARNESS_ASK_USER_TIMEOUT_S` | `null` |
| `harness.subagents.enabled` | `BOB_HARNESS_SUBAGENTS_ENABLED` | `true` |
| `harness.subagents.max_depth` | `BOB_HARNESS_SUBAGENTS_MAX_DEPTH` | `3` |
| `harness.delegation.enabled` | `BOB_HARNESS_DELEGATION_ENABLED` | `true` |

Image/container additions (compose/Dockerfile, not part of `HarnessConfig`): `PYDANTIC_AI_NO_BANNER=1`; `requirements.lock` regen installs `pydantic-ai-harness[coder]` extras. The `ask_user.answerer` wiring detail: `deferred` wires `AskUser(answerer=None)` and routes answers through the taskloop gate flow (D3); `inline` wires an `Answerer` backed by the gateway session/bus for interactive chat sessions within `timeout_s`. Capability on/off switches default to ON for the executor; a disabled capability is simply omitted from the executor agent's `capabilities=[...]` list (verified ctor param), and the executor still functions (delegation-capable even with memory/skills off — graceful degradation is asserted by tests in Part 2's phase plan).

---

## Part 2 — Phase-by-phase TDD plan, verified risk checklist, compatibility matrix

Reads Part 1 (D1–D7, target architecture §3.1–3.4) and the verified fact sheet. All phases build on D1–D7; no phase re-litigates a decision. **Canonical test invocation:** from `src/bob-harness/` (pytest `asyncio_mode=auto`, `pythonpath=[.]`): `pytest -q tests/test_X.py` per phase, full battery `pytest -m "not smoke"` before every commit; lint from repo root: `ruff check src/bob-harness/bob_harness src/bob-harness/tests`. **State snapshot:** after EVERY phase append to `/opt/data/results/2026-11-24/pai2-migration/STATUS.md`: phase id, commit sha(s), LOC touched, green-command summary, open blockers. Sessions reset tokens every 5h — each phase is scoped to fit 1–2 windows; a window never ends mid-phase without a committed green state. Commits co-authored with mjaniszew, never pushed.

## 4. Phase-by-phase TDD execution plan

Phase order: **deps first (P0) → smallest API surface (P1) → executor skeleton (P2) → capabilities one at a time (P3–P5) → gate redesign (P6) → consumer surface + default flip (P7) → bus/plumbing (P8) → docs/smoke (P9)**. Merge-safety key: ✅ independently committable & merge-safe on `custom-harness-poc` (no behavior change for existing operators); ⚠ behavior flip shipped with a config flag.

### P0 — Pins + lock regen + banner env (deps first)
- **Goal:** only the dependency surface moves to `pydantic-ai==2.73.0` + `pydantic-ai-harness[coder]==0.73.0`; the entire current battery stays green under the new venv; v2 import banner silenced at image/test edges. **No source behavior change.**
- **Failing tests first — `tests/test_pins.py` (new):**
  1. `test_pydantic_ai_major_is_2` — `importlib.metadata.version("pydantic-ai").startswith("2.")`.
  2. `test_harness_pinned_0730` — `version("pydantic-ai-harness") == "0.73.0"`.
  3. `test_coder_extra_importable` — `pydantic_ai_harness.coder` imports (proves json-repair present; verified ImportError without extra).
  4. `test_requirements_txt_cap_removed` — no `pydantic-ai>=1,<2` line; new rationale comment (m7 reason superseded by D3).
  5. `test_requirements_lock_v2_lines` — lock contains `pydantic-ai==2.73.0`, `pydantic-ai-slim==2.73.0`, `pydantic-ai-harness==0.73.0`, `json-repair` (and `pydantic-ai-graph`/`pydantic-ai-evals` 2.73.0 line).
  6. `test_banner_env_set` — `PYDANTIC_AI_NO_BANNER=1` present in `dockerfiles/Dockerfile.hermes` AND `compose.yaml` service env.
  7. `test_conftest_sets_no_banner` — `tests/conftest.py` sets the env var before any `pydantic_ai` import.
  8. `test_logfire_instrument_pydantic_ai_callable_under_v2` — `logfire.instrument_pydantic_ai` exists and one call succeeds in the 2.73.0 venv (supervisor try/except intact, supervisor.py:177-188).
- **Implementation steps:** (1) rewrite `src/bob-harness/requirements.txt` pins + DElibERATE-cap comment; (2) regenerate `requirements.lock` ONCE per policy (uv venv 3.13, `uv pip install -r requirements.txt`, freeze BEFORE dev tools); (3) add `ENV PYDANTIC_AI_NO_BANNER=1` to `dockerfiles/Dockerfile.hermes` + `compose.yaml`; (4) `tests/conftest.py` env guard; (5) one-off venv probe for row 8 + a 2.73.0 `Agent(…)` smoke import (documented in plan doc).
- **Green criteria:** `pytest -q tests/test_pins.py` → `pytest -m "not smoke"` (FULL battery offline under new venv — proves 1.x code runs on 2.73.0) → `ruff check src/bob-harness/bob_harness src/bob-harness/tests`.
- **Existing green:** all 31 test files (no exceptions — P0 must not touch any module behavior).
- **Commit:** `test: dependency-pin + banner-env battery (failing-first)` then `feat(deps): pin pydantic-ai 2.73.0 + pydantic-ai-harness[coder] 0.73.0, silence v2 import banner`.
- **Est LOC:** ~190 — files: `requirements.txt`, `requirements.lock`, `dockerfiles/Dockerfile.hermes`, `compose.yaml`, `tests/conftest.py`, `tests/test_pins.py` (src ~20, tests ~170).
- **Exit criteria:** lock shows 2.73.0 family + harness 0.73.0 with extras; full battery green offline; banner quiet in test/container logs; STATUS.md updated.
- **Merge-safe:** ✅

### P1 — Router v2 port (D7, smallest API surface)
- **Goal:** `RouterBrain` route/analyze/health_check on v2 `Agent` — capability-free, byte-identical contract (schema output, keyword fallback, `router_ledger.jsonl` rows incl. m4b banner).
- **Failing tests first — `tests/test_router_v2_port.py` (new):**
  1. `test_route_agent_has_empty_capabilities` — `brain._agent_route.capabilities == []` (AttributeError on v1: passes only under 2.73.0).
  2. `test_analyze_agent_has_empty_capabilities` — same for `brain._agent_analyze`.
  3. `test_run_result_response_and_output` — `run_sync` result exposes `.response`, and `isinstance(run.output, Route)` (2.73.0-verified surface).
  4. `test_run_usage_requests_and_all_messages` — `run.usage.requests ≥ 1` feeds `_split_latency`; `run.all_messages()` is a list (ledger path router.py:648-650).
  5. `test_model_settings_temperature_zero` — `isinstance(ModelSettings, pydantic_ai.ModelSettings)` with `temperature=0` preserved (router.py:465).
  6. `test_call_errors_tuple_v2_imports` — `_CALL_ERRORS` classes resolve from `pydantic_ai.exceptions` under v2 (router.py:61,129-139).
  7. `test_retry_notes_accept_v2_content_list` — synthetic message with v2-shaped `RetryPromptPart(content=list-of-details)` still yields one ≤120-char note (router.py:270-278).
  8. `test_budget_constants_unchanged` — `_PROMPT_CHAR_CAP=9000`, digest ≤600 ×3, index ≤1500 (guards TEST_PLAN row 11).
- **Implementation steps:** `router.py` only — ctor args already param-for-param (router.py:477-495); change imports if any alias moved (none verified), keep `run_sync`/`run` calls, `output_type`, retries, ledger internals, fallback tables, banner functions (router.py:861-885), `analyze`/`analyze_async` (router.py:892,921), `health_check` (router.py:953-980) untouched. Zero capability mounting (D7).
- **Green criteria:** `pytest -q tests/test_router.py tests/test_router_provider_down.py tests/test_router_v2_port.py` → `pytest -m "not smoke"` → `ruff check src/bob-harness/bob_harness src/bob-harness/tests`.
- **Existing green:** `test_router.py`, `test_router_provider_down.py`, `test_gateway_app.py`, `test_discord_bot.py`, `test_e2e_scenarios.py`, `test_supervisor.py`.
- **Commit:** `feat(router): port RouterBrain to pydantic-ai 2.x Agents (capability-free, D7)`.
- **Est LOC:** ~280 — files: `router.py` (~40), `tests/test_router_v2_port.py` (~240).
- **Exit criteria:** ledger/fallback/banner row shapes asserted unchanged; ping path green; STATUS.md updated.
- **Merge-safe:** ✅

### P2 — Executor adapter skeleton (D2; capabilities wired, OFF by default in tests)
- **Goal:** `executor` adapter implementing `AgentRun` on the v2 Agent + capability factory, registered in the adapters registry; config gains the `harness:` group; taskloop untouched (nothing selects `executor` yet — router `Literal` schema can't).
- **Failing tests first — `tests/test_adapter_executor.py` (new) + new cases appended to `tests/test_config.py`:**
  1. `test_registry_exposes_executor` — adapters registry contains `"executor"` satisfying `AgentRun`.
  2. `test_streams_started_and_final_events` — `run(spec)` yields `started` then `final` StreamEvents (closed kinds contract).
  3. `test_failed_event_on_model_error` — model exception → single `failed` StreamEvent, never raises.
  4. `test_spec_prompt_and_workdir_used` — spec.prompt is the user prompt; per-run `workspace=` from `spec.workdir`.
  5. `test_capabilities_empty_when_disabled` — all `harness.*` toggles off → `assemble_capabilities(...)` returns `[]` and executor still runs (graceful degradation).
  6. `test_oneway_spec_same_shape` — oneway flag carried; no gate/ask machinery engaged.
  7. `test_no_subprocess_spawn` — executor is in-process: never touches `start_process`/`build_child_env`.
  8. Config (in `tests/test_config.py`): `test_harness_group_defaults` parses Part 1 §3.4 YAML; `test_new_flat_env_bindings` (`BOB_HARNESS_MEMORY_ENABLED` et al. map via `_FLAT_ENV_BINDINGS`, config.py:191-200); `test_unknown_env_forbidden` — `extra="forbid"` intact for untouched keys.
- **Implementation steps:** (1) `adapters/executor.py` — AgentRun impl + `_build_agent(cfg, workdir)` + `assemble_capabilities(...)` returning per §3.4 toggles (all disabled-by-default in test cfg fixtures); (2) `config.py` — `harness:` sub-group `HarnessHarnessConfig` + 15 `_FLAT_ENV_BINDINGS` entries + `tasks.runner: Literal["analyzed","executor"] = "analyzed"` (dispatch switch for P7, default = today); (3) adapters registry registration (additive).
- **Green criteria:** `pytest -q tests/test_adapter_executor.py tests/test_config.py` → `pytest -m "not smoke"` → `ruff check src/bob-harness/bob_harness src/bob-harness/tests`.
- **Existing green:** `test_adapter_base.py`, `test_adapter_fake.py`, `test_adapter_hermes.py`, `test_adapter_opencode.py`, `test_taskloop.py`, `test_taskloop_invariant.py`, `test_config.py`, `test_config_behind_proxy.py`.
- **Commit:** `feat(executor): adapter skeleton + capability assembly (tests-off default) + harness config group (D2)`.
- **Est LOC:** ~760 — files: `adapters/executor.py` (~280), `config.py` (~140), tests (~330).
- **Exit criteria:** StreamEvent/failed-shape tests green; executor unreachable by any current routing path (merge-safe); battery green; STATUS.md.
- **Merge-safe:** ✅ (registry additive, unselectable until P7 flag).

### P3 — Delegation capability (D1)
- **Goal:** custom `AbstractCapability` exposing `delegate_run(...)` tool(s); hermes/opencode remain REAL subprocess adapters; zero harness `SubAgents` entries for CLIs.
- **Failing tests first — `tests/test_delegation_capability.py` (new):**
  1. `test_toolset_exposes_delegate_run` — `get_toolset()` exposes `delegate_run` with agent/profile/workdir/prompt params.
  2. `test_calls_registered_adapter` — invocations construct `RunSpec` (prompt/profile/workdir/resume_ref/oneway/files) and call the registered adapter's `run(spec)`.
  3. `test_hermes_profile_passthrough` — `simple|researcher` forwarded; invalid profile dropped via `_normalize_profile`-equivalent rules.
  4. `test_opencode_workdir_guard` — workdir must be under `/app/projects` (opencode validation, unchanged).
  5. `test_events_restreamed` — adapter StreamEvents merge into the executor run (`wrap_run_event_stream`), `final` forwarded.
  6. `test_child_env_contract` — subprocess path still via `build_child_env`; `BOB_HARNESS_*` never leaks (existing adapter asserts reused).
  7. `test_disabled_capability_omits_tool` — `harness.delegation.enabled=false` → tool absent from toolset.
  8. `test_no_subagents_entries_for_clis` — assembled capability list contains NO `SubAgents(agents=[…hermes/opencode…])` (D1 invariant).
- **Implementation steps:** new `bob_harness/delegation.py` (toolset-backed capability wrapping adapters registry; reuses fixtures/argv contracts); executor assembly adds it when enabled; docs-comment rationale block (D1).
- **Green criteria:** `pytest -q tests/test_delegation_capability.py tests/test_adapter_executor.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_adapter_hermes.py`, `test_adapter_opencode.py`, `test_adapter_base.py`, `test_taskloop.py`, `test_adapter_fake.py`.
- **Commit:** `feat(executor): delegate_run custom capability — hermes/opencode stay real subprocess adapters (D1)`.
- **Est LOC:** ~480 — files: `bob_harness/delegation.py` (~260), `adapters/executor.py` (~30 wiring), tests (~200).
- **Exit criteria:** argv/child-env snapshot tests untouched and green; D1 asserted; STATUS.md.
- **Merge-safe:** ✅ (executor still unselectable).

### P4 — Memory engine on FileMemoryStore (D4)
- **Goal:** `MemoryEngine` reimplemented over `FileMemoryStore(dir=<storage.data_dir>/memory, workspace=LocalWorkspaceBackend(...))` sharing ONE store with the harness `Memory` capability; plain-file format + char budgets + audit/digest byte-contracts preserved.
- **Failing tests first — `tests/test_memory_store_v2.py` (new):**
  1. `test_store_writes_plain_files_in_dir` — `store.write("MEMORY.md", …)` creates `<dir>/MEMORY.md`, nothing else (verified no sidecars).
  2. `test_store_requires_workspace` — `FileMemoryStore(dir)` w/o workspace raises `UserError` on first read/write (verified).
  3. `test_write_mutation_returns_version` — `MemoryMutation(version=<sha256>, replayed, existed)`; second identical write → `replayed=True`.
  4. `test_engine_budget_check_before_store_write` — over-budget MEMORY_EDIT → `MemoryBudgetError` AFTER the audit line (existing ordering contract).
  5. `test_engine_fenced_edit_blocks` — add-bullet idempotent, replace/remove first-line-match via store (same semantics as today).
  6. `test_memory_capability_reads_shared_store` — harness `Memory(store=…, agent_name='executor')` reads the same file the engine wrote.
  7. `test_memory_updated_event_and_audit_rows` — `memory.updated` bus event + `state/memory_audit.jsonl` rows unchanged.
  8. `test_digest_caps_unchanged` — `digest()` ≤600 each, honest ValueError budgets (memory_chars 2200 etc.).
- **Implementation steps:** (1) `memory.py` — store-backed `_persist_file` replacing `_atomic_write_text` for the three canonical files (torn-tail safety via store); fenced-edit/audit/digest stay engine-side; (2) executor assembly adds `Memory(...)` when enabled (default OFF in tests); (3) router/gateway call sites untouched (they consume `digest()`).
- **Green criteria:** `pytest -q tests/test_memory.py tests/test_memory_store_v2.py tests/test_router.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_memory.py` (FULL green, unchanged assertions), `test_router.py`, `test_disk_full.py`, `test_supervisor.py`.
- **Commit:** `feat(memory): MemoryEngine over harness FileMemoryStore, same plain files + budgets (D4)`.
- **Est LOC:** ~440 — files: `memory.py` (~220), `adapters/executor.py` (~20), tests (~200).
- **Exit criteria:** identical MEMORY.md/USER.md/lessons.md bytes for identical inputs; store-version round-trip; STATUS.md.
- **Merge-safe:** ✅

### P5 — Skills (D5)
- **Goal:** harness `Skills` capability mounts existing `<data_dir>/skills` folders; `SkillsEngine` keeps sole ownership of byte-deterministic INDEX.md + capture.
- **Failing tests first — `tests/test_skills_v2_mount.py` (new):**
  1. `test_capability_mounts_existing_folders` — 0.73.0 `Skills` discovers SKILL.md frontmatter (name/description) from existing folders.
  2. `test_missing_directories_skip_on_fresh_tree` — `missing_directories='skip'` boots on empty data dir.
  3. `test_missing_directories_ignore_is_valueerror` — `'ignore'` raises ValueError (verified; regression guard).
  4. `test_index_md_stays_engine_owned` — INDEX.md bytes unchanged with capability mounted (D5 invariant + R3 seed untouched).
  5. `test_capture_suffixes_and_event` — slug `-2`/`-3` suffixes + `skill.captured` event unchanged.
  6. `test_executor_gets_skill_toolset` — capability `get_toolset()` wired into executor assembly when enabled.
  7. `test_read_files_false_no_body_surface` — body access stays the explicit engine path (hard-truncate 4000 + marker).
- **Implementation steps:** executor assembly adds `Skills(directories=[<data_dir>/skills], missing_directories='skip', include/exclude=read-fom cfg, read_files=cfg, workspace=skills workspace)`; `skills.py` unchanged except type-share glue; supervisor R3 seed untouched.
- **Green criteria:** `pytest -q tests/test_skills.py tests/test_skills_v2_mount.py tests/test_supervisor.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_skills.py`, `test_supervisor.py`, `test_memory_store_v2.py`.
- **Commit:** `feat(executor): mount harness Skills capability on existing skill folders (D5; INDEX.md engine-owned)`.
- **Est LOC:** ~280 — files: `adapters/executor.py` (~50), tests (~220), `skills.py` (~10).
- **Exit criteria:** INDEX.md/CAPTURE byte-contracts asserted unchanged; STATUS.md.
- **Merge-safe:** ✅

### P6 — WAITING_INPUT deferred flow (D3) incl. persistence/restart survival
- **Goal:** executor questions park via v2 `DeferredToolRequests`; park persists `run.all_messages_json()` + a `DeferredToolResults` seed in the task folder; `answer()` rebuilds + resumes; FSM (`RUNNING ↔ WAITING_INPUT`), `gates.jsonl`, 24h timer, re-notify→`FAILED(gate_timeout)`, `/api/tasks/answer/{task_id}` and `gate.answered` bus contract EXACTLY as today; subprocess adapters keep prompt-append resume.
- **Failing tests first — `tests/test_gate_deferred.py` (new):**
  1. `test_executor_question_returns_deferred_requests` — AskUser tool `CallDeferred(kind='question')` → `DeferredToolRequests(calls, metadata)` (verified round-trip seam).
  2. `test_taskloop_parks_on_deferred` — deferred requests → `RUNNING→WAITING_INPUT`, gates.jsonl row + `deferred_messages.json` + `deferred_seed.json` in task folder.
  3. `test_gate_timer_renotify_then_failed` — default 24h timer, exactly one `gate.notify` re-notify, then `FAILED(gate_timeout)` (timer contract bytes unchanged).
  4. `test_answer_resumes_with_deferred_results` — `answer()` → `DeferredToolResults(calls={tool_call_id: value})` resume completes; transcript '## User answer' append preserved.
  5. `test_parked_task_survives_restart` — kill/re-create TaskLoop → replay restores gate row, re-arms timer, reloads seed, resume completes with the ORIGINAL in-run history (all_messages_json round-trip).
  6. `test_subprocess_adapters_keep_prompt_append_resume` — hermes/fake park+answer path byte-identical (no deferred machinery).
  7. `test_gateway_answer_endpoint_unchanged` — `POST /api/tasks/answer/{task_id}` drives the SAME resume path.
  8. `test_discord_gate_answered_consumed` — `gate.answered` bus topic drives the same `answer()` (taskloop.py:1304-1332 contract).
- **Implementation steps:** (1) taskloop gate engine branch: on stream-level deferred requests → park + persist (reuse `_atomic_write_text`/persist.JsonlWriter patterns); (2) `answer()` → rebuild executor from cfg + load seed (`agent.run(..., deferred_tool_results=…)`, verified signature param); (3) `replay()` (F17) re-arms timers AND loads seed files; (4) timeout path clears seed; (5) executor assembly wires `AskUser(answerer=None, timeout=None, id='ask_user')` when `harness.ask_user.answerer=deferred` (default).
- **Green criteria:** `pytest -q tests/test_gate_deferred.py tests/test_taskloop.py tests/test_replay.py tests/test_tasks.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_taskloop.py`, `test_taskloop_invariant.py`, `test_replay.py`, `test_tasks.py`, `test_gateway_app.py`, `test_discord_bot.py`, `test_e2e_scenarios.py`, `test_disk_full.py`.
- **Commit:** `feat(gate): WAITING_INPUT on the v2 deferred-tool flow with persisted resume seed; FSM/timers/API unchanged (D3)`.
- **Est LOC:** ~770 — files: `taskloop.py` (~300), `adapters/executor.py` (~60), `tasks.py` (~30 seed-schema), tests (~380).
- **Exit criteria:** deferred round-trip + restart-survival tests green; subprocess resume paths asserted identical; events.jsonl registry closed (no new kinds except optional seed notes — none added); STATUS.md.
- **Merge-safe:** ✅ (task dispatch still routed to hermes/opencode; executor gate path exercised only via direct adapter tests).

### P7 — Gateway + Discord updates + screenshot-ready e2e + runner default flip (⚠)
- **Goal:** gateway/app.py + discord_bot.py call-sites on the v2 surface (calls only — surface unchanged); `tasks.runner` default flips to `executor` (analysis `agent` field becomes the executor's delegation hint); screenshot-ready e2e; smoke scripts updated for the runner.
- **Failing tests first — `tests/test_gateway_v2_surface.py` (new):**
  1. `test_task_runs_on_executor_by_default` — default cfg (runner=executor): folder task → executor run → delegate_run(opencode) tool event visible in transcript; task registry events unchanged.
  2. `test_runner_analyzed_flag_rollback` — `tasks.runner="analyzed"` restores today's adapter pick exactly.
  3. `test_analysis_hint_used_by_executor` — analysis `agent`/`mode`/`workdir`/`prompt_seed` feeds the executor run prompt + delegate preference without schema change.
  4. `test_chat_stream_shape_unchanged` — SSE event frames byte-shape compatible (route → adapter stream relay).
  5. `test_discord_thread_home_channel_contract` — thread creation, home-channel routing, `gate.answered` → resume for executor tasks.
  6. `test_gateway_endpoints_unchanged` — `/api/tasks`, `/api/task/{id}`, `/api/tasks/answer/{task_id}`, `/api/schedule` CRUD shapes identical.
  7. `test_screenshot_ready_e2e` — scripted UI flow (login → chat → task board → answer form → fallback banner div) with DOM assertions in the state required for visual capture; documented capture commands in docs.
- **Implementation steps:** (1) `gateway/app.py` `_run_agent` + answer endpoint wiring; (2) `gateway/discord_bot.py` router/answer paths; (3) config default `tasks.runner="executor"` + add flat env `BOB_HARNESS_TASKS_RUNNER`; (4) `scripts/host-smoke.sh` — boot, `/healthz`, executor-backed task pickup check, no-banner assertion; screenshot e2e entry; (5) `ui.py` untouched.
- **Green criteria:** `pytest -q tests/test_gateway_v2_surface.py tests/test_gateway_app.py tests/test_gateway_ui.py tests/test_discord_bot.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_gateway_app.py`, `test_gateway_ui.py`, `test_discord_bot.py`, `test_sessions.py`, `test_e2e_scenarios.py`, `test_taskloop.py`.
- **Commit:** `feat(gateway): v2 agent calls + executor runner default (flag tasks.runner=analyzed|executor) + screenshot-ready e2e`.
- **Est LOC:** ~640 — files: `gateway/app.py` (~150), `gateway/discord_bot.py` (~90), `config.py` (~25), `scripts/host-smoke.sh` (~40), tests (~330).
- **Exit criteria:** full battery + e2e script green both with runner=executor AND analyzed (rollback proof); STATUS.md.
- **Merge-safe:** ⚠ — THE behavioral flip; revert = `BOB_HARNESS_TASKS_RUNNER=analyzed` (compose documented).

### P8 — taskloop/scheduler bus topics + CapabilityEvent bridge
- **Goal:** CapabilityEvent visibility (requirement 7 "consider bus topics"): AskUser capability events mirrored onto the EventBus; scheduler spawns unchanged through taskloop.
- **Failing tests first — `tests/test_capability_events_bridge.py` (new):**
  1. `test_ask_user_requested_event_publishes` — `AskUserRequestedEvent` (CapabilityEvent subclass, verified base fields kind/capability_id/tool_call_id/tool_name) → bus topic `harness.ask_user.requested`.
  2. `test_ask_user_answered_event_publishes` — `harness.ask_user.answered` payload w/ tool_call_id + task_id.
  3. `test_events_jsonl_registry_untouched` — no new kinds in the closed registry (topic additions only).
  4. `test_scheduler_spawn_through_taskloop` — `spawn_agent` job → inbox task → single-runner pickup unchanged (with executor runner).
  5. `test_scheduler_restore_with_exec_runner` — restore path green under new runner default.
  6. `test_unknown_capability_events_ignored` — subscriber tolerates unmapped CapabilityEvent kinds.
- **Implementation steps:** taskloop bus sink maps the two events (taskloop.py sink region ~1287-1332); `bus.py` topic registration additions; scheduler wiring untouched (APScheduler 3 job kinds + misfire + runs ledger).
- **Green criteria:** `pytest -q tests/test_capability_events_bridge.py tests/test_bus.py tests/test_scheduler.py tests/test_scheduler_battery.py tests/test_taskloop_schedule_demand.py` → `pytest -m "not smoke"` → `ruff check …`.
- **Existing green:** `test_bus.py`, `test_scheduler.py`, `test_scheduler_battery.py`, `test_taskloop_schedule_demand.py`, `test_taskloop_invariant.py`, `test_replay.py`.
- **Commit:** `feat(bus): CapabilityEvent bridge topics for ask_user; scheduler task spawns unchanged (req 8)`.
- **Est LOC:** ~250 — files: `taskloop.py` (~80), `bus.py` (~15), tests (~155).
- **Exit criteria:** bridge tests + scheduler battery green; STATUS.md.
- **Merge-safe:** ✅ (additive topics; consumers ignore unknown kinds).

### P9 — Docs + smoke scripts + final review
- **Goal:** OVERVIEW/ARCHITECTURE/CONFIGURATION v2 sections + finalized plan doc; updated host/in-container smoke runbooks; closure review.
- **Failing tests first — `tests/test_docs_smoke_contract.py` (new):**
  1. `test_docs_record_v2_pins` — OVERVIEW/ARCHITECTURE/CONFIGURATION reference pydantic-ai 2.73.0 + harness 0.73.0.
  2. `test_architecture_documents_capability_manifest` — D1–D7 rationale + §3.2 manifest rendered.
  3. `test_configuration_documents_harness_group` — `harness:*` keys + flat env table + `tasks.runner` documented.
  4. `test_host_smoke_v2_checks` — `scripts/host-smoke.sh` asserts lock pins, banner quiet, `/healthz`, executor task pickup.
  5. `test_dev_incontainer_script_contract` — `scripts/dev-incontainer-tests.sh` still `cd src/bob-harness` + `pytest -m "not smoke"`.
- **Implementation steps:** docs edits; script updates; final review pass (opencode reviewer, ≤3 fix rounds); verify DoD checklist from task doc; STATUS.md final entry.
- **Green criteria:** `pytest -q tests/test_docs_smoke_contract.py` → `pytest -m "not smoke"` (battery) → `bash scripts/dev-incontainer-tests.sh` (host) → `ruff check src/bob-harness/bob_harness src/bob-harness/tests`.
- **Existing green:** full battery.
- **Commit:** `docs: v2 architecture/configuration sections + smoke runbooks (final review)`.
- **Est LOC:** ~620 — files: `docs/OVERVIEW.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/plans/10-pydantic-ai-v2-migration-plan.md`, `scripts/host-smoke.sh`, tests (~90).
- **Exit criteria:** all DoD items checked off; battery green in-container; STATUS.md closed.
- **Merge-safe:** ✅ (docs/scripts only).

**Totals:** ~4.6k LOC (src ~1.6k, tests ~2.3k, docs/scripts ~0.7k); independently mergeable phases P0–P6, P8, P9; single flip-phase P7 behind `tasks.runner`.

## 5. Verified risk checklist

Intro: greped the current repo per symbol (`router.py`, `taskloop.py`, `memory.py`, `skills.py`, `config.py`, `supervisor.py`, `gateway/*`, requirements files, tests) and checked every v2-facing item against live probes of pydantic-ai 2.73.0 + pydantic-ai-harness 0.73.0 (python 3.13 venv; facts in the context sheet). Statuses: VERIFIED OK (probed directly), CHANGED (works but semantics/versions move), NEW (1.x has no counterpart).

| # | Symbol/API (file:line) | 1.x usage | 2.73.0 status | Migration action | Risk |
|---|---|---|---|---|---|
| 1 | `Agent(model=, name=, output_type=, model_settings=, retries=)` — router.py:477-495 | ctor for route/analyze agents ×2 | VERIFIED OK (identical params; `capabilities` available but unused here, D7) | zero ctor-arg changes; P1 port is mechanical | L |
| 2 | `run_sync`→`run` + `run.output` — router.py:717,746,961 | schema result consumed via `run.output` | VERIFIED OK (`run.output` isinstance-validated; internally delivered as final-result tool call — surface unchanged) | keep calls; P1 test asserts `.response` exists too | L |
| 3 | `run.usage.requests` — router.py:648 | latency split + retry-count rows | VERIFIED OK (field present) | unchanged (row 648 int-cast retained) | L |
| 4 | `run.all_messages()` — router.py:650 | `_retry_notes` scan | VERIFIED OK | unchanged; P6 reuses `all_messages_json()` for the gate seed | L |
| 5 | `ModelSettings(temperature=0)` — router.py:60,465 | deterministic schema calls | VERIFIED OK (imports from `pydantic_ai`) | unchanged | L |
| 6 | exceptions imports — router.py:61,129-139 | `_CALL_ERRORS` tuple + retry arithmetic (router.py:683) | VERIFIED OK (`UnexpectedModelBehavior`, `ModelHTTPError`, `ModelAPIError` import) | unchanged | L |
| 7 | `RetryPromptPart` — router.py:62,270-278 | retry-note extraction `json.dumps(part.content)` ≤120ch | CHANGED (import OK; `content` may now be `list[ErrorDetails]`, not only str → ledger note bytes could differ on genuine retries) | keep `dumps(default=str)` slicing; P1 test pins the list-content shape; note-only-on-retry guard | M |
| 8 | `OpenAIChatModel` + `OpenAIProvider(base_url, api_key)` — router.py:63-64,458-461 | model/provider construction | VERIFIED OK (constructs) | unchanged | L |
| 9 | `output_type=Route/Analysis` — router.py:480,492 | schema-enforced structured output | VERIFIED OK (TestModel round-trip validates `run.output`) | unchanged; router mock seam (`model=` injection) unaffected | L |
| 10 | import banner side-effect (`PYDANTIC_AI_NO_BANNER`) — router.py:60 first `pydantic_ai` import | v1: no import output | CHANGED (banner prints on import; silencing env verified) | `ENV PYDANTIC_AI_NO_BANNER=1` in Dockerfile.hermes + compose + tests/conftest guard (P0) | M |
| 11 | `pydantic-ai-slim`/`-graph`/`-evals` lock lines — requirements.lock:110-113 (also `ag-ui-protocol==1.0.0` :1 pulls fastapi/starlette) | 1.107.10 family frozen | CHANGED (regen to 2.73.0 family + harness 0.73.0 + json-repair) | single P0 regen per lock policy; gateway battery re-run immediately after; freeze comment kept | M |
| 12 | `logfire.instrument_pydantic_ai()` — supervisor.py:186 | instruments v1 agent runs | CHANGED (function exists in logfire 6.0.1; v2 span/attribute mapping differs — probed import-level only) | verify one live call under 2.73.0 at P0; supervisor try/except (177-188) stays as containment | M |
| 13 | fastapi/starlette pin interplay — requirements.txt:7 (`fastapi>=0.118,<1`), lock:36 `fastapi==0.165.0`, lock:136 `starlette==1.10.0`, lock:135 `sse-starlette==3.7.1` | SSE gateway + slowapi | CHANGED (lock regen may bump within `<1` cap) | pin exact resolved versions in new lock; run `test_gateway_app.py`/`test_gateway_ui.py` right after P0 regen | M |
| 14 | `capabilities=` ctor param (executor) — NEW adapters/executor.py | n/a (1.x had no params) | NEW, VERIFIED OK (`Agent(capabilities=[...])`; per-run `workspace=` on `run/run_sync`) | mount Memory/Skills/AskUser/delegate_run per §3.2 only on executor; router stays capability-free; off-state graceful-degradation tests (P2) | L |
| 15 | deferred tool machinery — NEW (taskloop.py:311-335, 1304-1332 is the 1.x prompt-append park/answer) | v1: prompt-append + gates.jsonl only | NEW, VERIFIED END-TO-END (`CallDeferred(kind='question'|'external')` → `DeferredToolRequests(calls, approvals, metadata, validated_args)` → resume via `DeferredToolResults(calls, approvals, metadata, answered_by)`) | D3 flow (P6): park + persist `all_messages_json` + seed; `answer()` resume; replay reload; subprocess adapters keep prompt-append | M (highest behavioral risk) |
| 16 | `FileMemoryStore` workspace requirement — NEW (memory.py 1-401 hand-rolled `_atomic_write_text`) | plain-file engine writes | NEW, VERIFIED (workspace required else `UserError`; writes plain files INSIDE dir, no sidecars; `write`→`MemoryMutation(version=sha256, replayed, existed)`, `read(path, max_chars)`→`MemoryFile`) | engine over store with `LocalWorkspaceBackend(working_dir=…)`; budgets/audit/digest remain engine policy (P4) | M |
| 17 | `Skills.missing_directories` enum — NEW (skills.py + supervisor R3 seed) | n/a | NEW, VERIFIED (`'error'|'skip'` only; `'ignore'` → ValueError) | mount with `'skip'` (fresh data trees); engine keeps INDEX.md/capture (P5) | L |
| 18 | v2 `AgentRun` event stream — NEW (adapters/base.py StreamEvent contract is ours) | adapter-run protocol ours | NEW (harness events ride `ctx.emit`; `wrap_run_event_stream` hook available) | executor re-streams harness/tool events into existing StreamEvent kinds only (P2/P3) | M |

**Residual risks (untestable at plan time) + mitigations:**
1. **Real-provider wire behavior** (structured output at temperature 0 on the OpenAI-compatible endpoint behind `effective_router_settings`) — probes used test models/ctor-level only. Mitigation: P1 keeps fallback + ledger as decision floor; `health_check` ping in container smoke (P0/P9) validates the live path; never-blocking contract covers failures.
2. **`all_messages_json()` serialization drift** across 2.73.x patch releases could break seed reload. Mitigation: exact pin; P6 round-trip restart test; document upgrade caveat (re-park tasks after version bumps).
3. **Logfire span mapping under v2** (attribute names, nested run graphs). Mitigation: existing try/except containment; not a DoD blocker; note in CONFIGURATION.
4. **Harness `Memory` `max_tokens` (token-based) vs char budgets** divergence. Mitigation: injection is capability-side only; engine-side caps keep tests authoritative; `inject_memory` toggle allows opt-out.
5. **`[coder]` extra build weight/binary compat** (pillow + ripgrep in slim image). Mitigation: host-smoke docker build check (P0/P9); pin resolver output in lock.
6. **Lock-regen drift between environments** (uv resolution differs by date/platform). Mitigation: single regen session, immediate commit, offline battery rerun; never regen mid-phase.
7. **Hermes/opencode fixture drift** from indirect refactors (delegation must not touch argv builders). Mitigation: existing snapshot fixtures (`tests/fixtures/hermes-*.out`, `test_adapter_*.py`) must stay untouched and green in every phase battery.

## 6. Compatibility matrix

| Feature | Current implementation (file) | v2 target | Risk |
|---|---|---|---|
| chat-turn routing | `RouterBrain.route/route_async` (router.py:806,834) on v1 Agent | capability-free v2 Agents, identical schema contract (D7) (P1) | L |
| task analysis | `RouterBrain.analyze/analyze_async` (router.py:892,921) | v2 port byte-identical (P1); `agent` field reused as executor delegation hint when runner=executor (P7) | M |
| keyword fallback | `_FALLBACK_KEYWORDS`/`_fallback_*` (router.py:121-124,761-778) | tables + deterministic decisions identical (P1) | L |
| router ledger | `JsonlWriter` → `state/router_ledger.jsonl` rows (router.py:638-693) | row shapes incl. retry notes/split latency identical (P1) | L |
| provider fallback banner | `_banner_event`/`_schedule_banner` (router.py:865-885), ui banner (gateway/ui.py) | m4b transcript line + ledger half unchanged (P1) | L |
| health ping | `health_check` + `kind=ping` row (router.py:953-980) | same ping on v2 agent (P1) | L |
| hermes adapter | `adapters/hermes.py` (argv snapshots, `--resume`, HERMES_HOME) | unchanged real subprocess; first-class delegate target (P3) | L |
| opencode adapter | `adapters/opencode.py` (`--session/--continue`, workdir guard) | unchanged real subprocess; first-class delegate target (P3) | L |
| fake adapter | `adapters/fake.py` scenario-driven | unchanged test seam; reused for delegation tests (P2) | L |
| task queue pickup | taskloop inbox watcher + single runner queue (taskloop.py) | unchanged; adapter selection switch `tasks.runner` (P2, P7) | M |
| FSM/transitions | `tasks.py` TRANSITIONS + `state.json` | unchanged incl. `RUNNING ↔ WAITING_INPUT` (P6) | L |
| gate park/answer | prompt-append park + gates.jsonl (taskloop.py:311-335) | deferred-flow park + persisted seed; appends preserved (P6) | M |
| gate timeout | 24h timer, one re-notify, `FAILED(gate_timeout)` (taskloop.py) | identical; timer re-armed by replay; timeout clears seed (P6) | M |
| replay | F17 boot replay (taskloop.replay) | gate rows restored + deferred seed reload + timers re-armed (P6) | M |
| SUBTASK_DROP | D17 chaining (taskloop.py/tasks.py) | exactly preserved; harness-only dispatcher (P2,P7) | L |
| sole invoker | taskloop single runner (`tasks.max_concurrent` default 1) | preserved; executor runs nested, never task-origin (D2) (P2) | L |
| scheduler job kinds | `spawn_agent`/`run_script`/`drop_task` (scheduler.py) | unchanged + executor-compatible spawns via taskloop sink (P8) | L |
| scheduler restore | runs ledger restore + misfire policy (scheduler.py) | unchanged (P8) | L |
| memory files+budgets | `MemoryEngine` MEMORY.md/USER.md/lessons.md + budgets (memory.py) | `FileMemoryStore` shared dir; same files + char budgets (D4) (P4) | M |
| memory audit | `state/memory_audit.jsonl` (memory.py) | engine-side audit unchanged (P4) | L |
| digest | `digest()` ≤600×3 projections (memory.py, router.py:501-509) | unchanged projection caps (P4) | L |
| skills INDEX | `SkillsEngine` generated INDEX.md, budget 1500, R3 seed (skills.py) | byte-deterministic, engine-owned; harness capability mounts folders alongside (P5) | L |
| skill capture | `capture()` SKILL_DRAFT, `-2/-3` suffixes, `skill.captured` (skills.py) | unchanged (P5) | L |
| skill body loading | `get_skill_body` hard-truncate 4000 + marker (skills.py) | unchanged; optional capability toolset for executor (P5) | L |
| gateway endpoints | `gateway/app.py` `/api/*` incl. `/api/tasks/answer/{task_id}` (app.py:856) | identical surface; internal calls to v2 surface only (P7) | L |
| SSE stream | `/api/chat/stream` event relay (gateway/app.py) | identical frame shapes; route→adapter→Session streaming unchanged (P7) | L |
| discord threads/home channel | `gateway/discord_bot.py` (create_thread, home channel, `gate.answered` consumer at :256) | unchanged surface; answers drive deferred resume (P7) | L |
| config env map | `_FLAT_ENV_BINDINGS` + `_FlatEnvSource` (config.py:191-200,291) | + `harness.*` group (§3.4) + 15 flat envs + `BOB_HARNESS_TASKS_RUNNER` (P2,P7) | L |
| logfire instrumentation | `logfire.instrument_pydantic_ai()` (supervisor.py:186) | retained w/ verification + try/except containment (P0) | M |
| smoke scripts | `scripts/host-smoke.sh`, `scripts/dev-incontainer-tests.sh` | v2 pins, no-banner assertion, executor pickup + screenshot-ready e2e (P0,P7,P9) | L |

---

## Part 3 — Open questions, state snapshot procedure, execution protocol, appendices

Reads Part 1 (D1–D7, target architecture §3.1–3.4), Part 2 (phases P0–P9, risk checklist §5, compatibility matrix §6) and the verified fact sheet. Part 3 closes the plan: open questions with decided defaults, the state-snapshot procedure that makes the work survive 5h token-budget resets, the execution/review/proof-of-work protocol, and the command crib the coder agent runs verbatim. Consistency rules: no OQ re-litigates D1–D7; every default below is cross-checked against Part 2 phase exits with the same phase numbers and D-numbers.

## 7. Open questions with defaults

Each OQ: question → why it matters → options → **DECIDED DEFAULT** (proceed-on-best-judgment; every default must stay consistent with D1–D7 and the Part 2 phase contracts). Defaults are binding unless the operator overrides in writing; an override is recorded in `STATUS.md` under `blocking issues`.

### OQ1 — Should SubAgents (nested) be enabled at launch or deferred post-migration?
- **Why it matters.** Part 1 §3.2/§3.4 mount the harness `SubAgents` capability on the executor (in-process AbstractAgent helpers, `max_depth=3`, read-only entries) with a default `enabled: true`. But no phase in Part 2 budgets building the `nested_helper` AbstractAgent (P2 assembles capability flags; P3–P5 cover delegation/memory/skills; P6 gate; P7 gateway). Enabling at the P7 flip would ship a capability whose entries were never implemented — dead config at best, unexpected in-process run graphs at worst during the single riskiest phase.
- **Options.** (a) enabled at launch: build the nested helper AbstractAgent as an extra work item; (b) deferred: plumb the flag + assembly now, ship disabled, enable post-migration; (c) drop SubAgents entirely (violates Part 1 §3.2 manifest).
- **DECIDED DEFAULT (b — deferred).** Refines Part 1 §3.4: the `harness.subagents` group, flat env (`BOB_HARNESS_SUBAGENTS_ENABLED`), and assembly path are built and OFF-tested in P2, but the shipped config at the P7 flip is `harness.subagents.enabled: false` with zero configured `SubAgent` entries ⇒ the capability is inert at launch. Enabling (`true` + a real internal `nested_helper` AbstractAgent) is a documented post-migration follow-up task, gated on one week of executor burn-in. Consistency: D1's "delegation via custom capability" and D6's "adopt only what the store workspaces need" are untouched; task DoD #2 ("Hermes/OpenCode as subagents for delegated work") is fully carried by the `delegate_run` capability (P3) plus the P7 default flip — SubAgents nesting is not required by any DoD item.

### OQ2 — Executor streaming granularity: `StreamEvent` chunk relay vs final-only?
- **Why it matters.** Part 2 P2 green-tests only `started`+`final`. Subprocess adapters stream progressively into Session transcripts and the SSE chat; a final-only executor would make the UI and gateway `_run_agent` appear frozen during multi-tool agentic runs (minutes), a behavior regression vs DoD #3 (chat behavior preserved).
- **Options.** (a) final-only relay (smallest surface, cold UI); (b) full-parity relay `started/chunk/tool/final/failed/system` (matches existing closed-kind contract); (c) chunk relay behind a config flag.
- **DECIDED DEFAULT (b — full-parity relay, phased).** P2 ships `started`+`final`+`failed` (as tested); P3 completes the relay — `chunk` mapped from v2 model text deltas and `tool` emitted per completed tool invocation (including nested `delegate_run` streams merged via `wrap_run_event_stream`, per P3 test 5). No new StreamEvent kinds are introduced (closed set preserved, P2 test seam unchanged); transcript char budgets and `_PROMPT_CHAR_CAP` guards are untouched (router-side budget rows asserted in P1). A config flag is rejected: the parity contract is not negotiable per environment.

### OQ3 — AskUser inline answerer for interactive chat sessions: build now or route all questions through the gate flow?
- **Why it matters.** Part 1 §3.4 enumerates `harness.ask_user.answerer: deferred|inline|none`. An `inline` Answerer = gateway-session wiring (chat-scoped bus + `timeout_s`) that no Part 2 phase budgets; chat turns at launch still route to hermes/opencode adapters whose park/answer path is the existing prompt-append resume (P6 test 6), so executor AskUser cannot occur in chat context before/after the flip.
- **Options.** (a) build inline answerer during migration (gateway session + timeout plumbing in P7); (b) route ALL questions through the deferred gate flow; inline stays a config option for post-migration.
- **DECIDED DEFAULT (b — gate flow only).** The only shipped mode at the P7 flip is `answerer: deferred` (Part 1 §3.4 default, D3): the run parks via `DeferredToolRequests`, TaskLoop persists the seed, and answers arrive via `/api/tasks/answer/{task_id}` or the Discord `gate.answered` path (P6 tests 4, 7, 8). `inline` remains an enumerated, tested-for-parse config value whose runtime wiring is a post-migration follow-up; `none` likewise parseable but disabled-by-omission. Consistency: D3 (deferred park/persist/resume) and DoD #3 (gateway/Discord surface unchanged) hold; the 24/7 autonomy requirement rejects blocking inline waits.

### OQ4 — CapabilityEvent→EventBus bridge topic naming + do events land in Session transcripts?
- **Why it matters.** Requirement 7 says "consider CapabilityEvent bus topics"; P8 tests already pin `harness.ask_user.requested`/`harness.ask_user.answered`. Undecided: the general naming scheme for future capability events, and whether bridged events should also be written into Session `transcript.jsonl`.
- **Options.** (a) ad-hoc topic names per event; (b) scheme `harness.<capability>.<event>`: ; (c) topics + transcript/system StreamEvent injection; (d) topics only, no transcript writes.
- **DECIDED DEFAULT (b + d).** Topic scheme `harness.<capability>.<event>: ` — launch ships exactly `harness.ask_user.requested` and `harness.ask_user.answered` (P8 tests 1–2), payloads carry `tool_call_id` + `task_id`; the taskloop sink consumes them for the gate flow (taskloop.py sink region, Part 2 P8). Bridged events do NOT land in Session transcripts and add NO kinds to the closed `events.jsonl` registry (P8 test 3, P6 exit criteria) — gate visibility already exists via `gates.jsonl` rows and `gate_opened`/`gate_answered` task events. Unknown/unmapped CapabilityEvent kinds are ignored by subscriber design (P8 test 6). Consistency: transcript parity (OQ2, DoD #3) and the closed-registry invariant.

### OQ5 — Deferred-gate resume: write a new TaskLoop event kind, or reuse existing events?
- **Why it matters.** `tasks.py` `events.jsonl` is a closed registry (appeared, state_change, gate_opened, gate_answered, note, subtask_drop, subtask_refused, subtask_schedule_note, subtask_schedule_accepted, dep_wait, result_written). Extending it changes the registry contract and every consumer that whitelists kinds.
- **Options.** (a) extend the registry with a new kind (e.g. `gate_deferred_answered`); (b) reuse `gate_answered` with seed metadata in the row payload (`note` for non-gate annotations); (c) write nothing extra.
- **DECIDED DEFAULT (b).** The resume path appends the existing `gate_answered` row (its payload gains seed metadata keys — `tool_call_id`, seed file names); `state_change` rows continue to cover `RUNNING ↔ WAITING_INPUT` exactly as today; `note` covers any extra annotation. No registry extension: P6's exit criteria already assert "events.jsonl registry closed — no new kinds", and P8 test 3 re-asserts it. Consistency: requirement 5 (map `tasks.py` WAITING_INPUT to the new flow) is satisfied FSM-first, registry-last.

### OQ6 — Memory digest source for the router after P4: `MemoryEngine.digest()` unchanged vs direct FileMemoryStore versioned reads?
- **Why it matters.** After P4 the engine and the harness `Memory` capability share one `FileMemoryStore` (D4). The router consumes ≤600-char digests ×3 and must never touch memory files directly (D7, capability-free, `_PROMPT_CHAR_CAP` row-11 budget pinned by tests). Allowing direct store reads from `router.py` would couple the router to SDK internals and risk budget drift.
- **Options.** (a) router reads versions via `FileMemoryStore.read(path, max_chars)` itself; (b) `MemoryEngine.digest()` keeps its exact signature/budget/cap contract, and internally re-points its file reads at the shared store; (c) keep engine reads on raw files (skip store on the read path).
- **DECIDED DEFAULT (b).** `MemoryEngine.digest()` is unchanged as a contract (P4 test 8 pins ≤600 caps and honest budget errors); its internal reads go through the shared `FileMemoryStore` (single source of truth with the `Memory` capability, version tracking for free) while byte-identical projections are guaranteed by the unchanged `test_memory.py` battery. The router keeps calling the engine — no store imports in `router.py` (D7 invariant, asserted in P1's capability-free tests).

### OQ7 — Boot replay rebuilding the `DeferredToolResults` seed: placeholder-answer all calls vs fail-fast?
- **Why it matters.** F17 boot replay (P6 test 5) reloads `deferred_messages.json` (all_messages_json) + `deferred_seed.json` to re-arm timers and finish parked gates. If the seed file is torn, missing, or references unknown `tool_call_id`s, the loop must decide: fabricate answers or mark the task failed.
- **Options.** (a) seed all calls with a placeholder value ("(unavailable — recovered from restart)") and resume; (b) fail-fast: park stays parked if `deferred_messages.json` is intact but the seed is reconstructible from it; otherwise mark `FAILED` with a `gate_corrupt` reason, preserve both files for forensics; (c) fail-fast immediately without reconstruction attempt.
- **DECIDED DEFAULT (b — reconstruct-then-fail-fast, no placeholders).** Replay order: load `deferred_messages.json` → if `deferred_seed.json` is unreadable/incomplete BUT messages are intact, rebuild the seed from the recorded `DeferredToolRequests` (deterministic, tested) and resume; if either is unrecoverable, the parked task goes `RUNNING/WAITING_INPUT → FAILED(gate_corrupt)` via the existing FSM `state_change` + gates.jsonl close — mirroring the `FAILED(gate_timeout)` precedent — with seed + messages files preserved untouched for audit and a `note` event appended. Placeholder answers are rejected outright: they fabricate user input into the model transcript, violating transcript honesty and DoD #3's behavior-preservation spirit. `gate_corrupt` is a FAILED *reason value*, not a new event kind (OQ5 holds). Seed writes use the atomic text-writer pattern, making torn seeds a should-never-happen path (residual risk 2, Part 2 §5).

### OQ8 — `requirements.lock` regen: strictly scope to pydantic-ai/harness deps, or bump unrelated stale pins too?
- **Why it matters.** The current lock is frozen R1 (pydantic-ai 1.107.10 family + fastapi 0.165.0/starlette 1.10.0/sse-starlette 3.7.1 within `requirements.txt` caps). P0 (Part 2) regenerates the lock ONCE; the question is whether the regen hand-scopes changes or re-resolves everything.
- **Options.** (a) hand-patch only pydantic-ai/harness lines, leave all else byte-identical; (b) full uv re-resolution from `requirements.txt` (caps unchanged except the pydantic-ai cap flip + harness pin), freeze whatever resolves; (c) full re-resolution after also lifting stale global caps fastapi etc.
- **DECIDED DEFAULT (b — full re-resolution within unchanged caps).** The ONLY `requirements.txt` edits are the pydantic-ai cap flip (`>=1,<2` → `>=2,<3`) with the superseded-rationale comment (m7 → D3) and the `pydantic-ai-harness[coder]==0.73.0` pin. uv re-resolves the coherent set; unrelated pins MAY drift within existing caps (fastapi/starlette/sse-starlette — risk rows 11/13), the resolved exact versions are frozen, and the gateway battery (`test_gateway_app.py`, `test_gateway_ui.py`) re-runs immediately after regen. Hand-patching (a) is rejected: a uv-resolved lock is a coherent DAG, and cherry-edited pins invite inconsistent transitive sets; (c) is rejected: out-of-cap bumps are out of scope. Freeze policy in Appendix A ("freeze BEFORE dev tools", runtime-only, `requirements-dev.txt` untouched).

## 8. State snapshot procedure (tokens-reset resilience)

The migration must survive session/token-budget resets every 5h (task dev rules) and produce a state file so any future session (human or agent) can resume mid-phase without archaeology.

### 8.1 `STATUS.md` — path, format, writer
- **Path:** `/opt/data/results/2026-11-24/pai2-migration/STATUS.md` (agent-writable results dir; NOT part of the git tree).
- **Writer:** the coder agent itself, inside the same OpenCode build run that executes the phases — updated synchronously with every phase commit and every review round (never batched, never at "end of day").
- **WHEN updated:** (1) after EVERY phase commit — both the phase-opening failing-tests commit (state `red-tests`) and the green closing commit (state `green`, battery + ruff summaries); (2) after EVERY review round — verdict, blocking vs editorial findings, fix commit hashes, round counter `r<k>`; (3) whenever a blocking issue is opened/closed; (4) at every PoW artifact capture (pointer row).
- **Format (exact fields):**

```
# PAI2 migration — STATUS
last_updated: <UTC ISO8601>
branch: custom-harness-poc   HEAD: <sha>
current_phase: P<n>  state: planning|red-tests|implementing|green|in-review|review-fix-r<k>|done
phases:
  P0: done    commits: <sha-open>, <sha-green>   battery: pytest -m "not smoke" green@<sha-green>
  P1: done    commits: <sha-open>, <sha-green>   review: r2 PASS (fix sha <sha>)
  P2: done    commits: <sha-green>               review: skipped (optional, battery clean)
  P3: in-review  open: <sha>  next: answer reviewer round r1
  ...
next_steps:
  - <verbatim next action, e.g. "write tests/test_gate_deferred.py::test_answer_resumes_with_deferred_results (red)">
  - <then: implementation step reference from Part 2 §4 P<n>>
blocking_issues:
  - none  |  ID-<n>: phase, symptom, evidence path, opened <ts>
review_log:
  P1 r1: PASS-with-notes (2 editorial) ; P4 r1: 1 blocking -> fixed r2 ...
pow:
  screenshots: pow/screenshots/ (P7 set: ...) ; api: pow/api/ ; tasks: pow/tasks/
```

Required content per the task contract: current phase, last commit hash per phase, next steps, blocking issues — plus review log and PoW pointers so no session re-derives any of it.

### 8.2 Per-phase resume protocol (after ANY reset, fresh session)
1. **Read `STATUS.md` in full** — current phase + state, per-phase commit hashes, verbatim next steps, blockers, review log.
2. **Verify repo state:** `git log --oneline -15` and `git status` on `custom-harness-poc`; HEAD must match STATUS's recorded hashes; a dirty tree is not allowed to carry over silently — follow the STATUS note (stash or finalize).
3. **Run the phase's pytest subset** from Appendix A expecting the exact red/green fingerprint recorded in STATUS (e.g. "P6 red: test_gate_deferred.py 7 failed" → re-run confirms the same failures); full battery only if STATUS says the phase is being closed.
4. **Continue exactly at `next_steps`:** failing-test-first → implement → green → commit (`Co-Authored-By: mjaniszew`) → append STATUS block. Never restart a phase from scratch while its recorded commit chain exists; never skip a recorded review round.

### 8.3 5h token-budget reset checkpoints (20–30 min scheduled checks)
- Scheduled checks every 20–30 min per task-file rule; at EACH check the coder appends one STATUS-internal checkpoint line (timestamp UTC, current phase/state, HEAD, last-green commit, est. minutes to the 5h boundary).
- **Boundary rule:** each phase is scoped to fit 1–2 windows and a window never ends mid-phase without a committed state (Part 2 header). Concretely: if < 40 min remain before a reset boundary, do NOT open a new failing-test cycle; land the current step (or the phase-red commit), commit, update STATUS, and stop. If a window DOES end mid-phase on red, STATUS must record the exact failing-test list + next implementation step — that is precisely what the Appendix B RESUME prompt continues from.
- Reset boundary ledger lives in STATUS under `current_phase`/`next_steps`; the RESUME prompt (Appendix B) is the paste-once session opener and makes the protocol self-executing.

## 9. Execution protocol, review and proof of work

### 9.1 Repo workflow
- **Branch:** all work on `custom-harness-poc` (current HEAD 7200ad3). No work happens on main/other branches; the branch is never pushed and never merged during the migration — merge decision is an explicit operator action after P9 closure + DoD sign-off.
- **Commits:** commit often — at minimum (a) the phase-opening failing-tests commit, (b) the green implementation commit, (c) each review-fix round, (d) PoW/doc touch-ups. Every commit carries the co-author trailer:
  `Co-Authored-By: mjaniszew` (name-only trailer, matching task rule "co-authored with mjaniszew"; existing branch convention shows the reviewer trailer under `Co-Authored-By:`).
- **NEVER push:** no `git push` of any kind (no force, no dry-run push, no remote branches). Status sharing happens via STATUS.md + the local repo.
- **Message style:** conventional type prefixes as on-branch (`test:` for red commits, `feat:`/`docs:`/`chore:` for green) with the phase's Part 2 commit lines used verbatim (P0–P9 commit messages are pinned in Part 2 §4).

### 9.2 Code review (opencode reviewer, max 3 rounds)
- **Reviewer:** the coder-agent (opencode) code-reviewer per task rules ("Code review via coder agent (opencode) reviewer; max 3 rounds of fixes"), invoked from the host with the repo checked out on `custom-harness-poc` (invocation crib in Appendix A). Review scope per round: the phase's open→green commit range (from STATUS), against the phase goal + exit criteria in Part 2 §4 and the D-decision it implements.
- **Mandatory review phases:** P1 (router v2 port — contract bytes), P3 (delegation capability — D1 surface), P4 (memory store — data integrity), P5 (skills mount — index contract), P6 (deferred gate — highest behavioral risk), P7 (gateway flip — DoD #3 surface).
- **Optional review phases:** P2 and P8 — reviewed only at coder discretion, mandatory triggers: battery anomaly beyond the phase's new tests, LOC > 25% over the Part 2 estimate, or any touched file outside the phase's declared file list. P0 gets no dedicated round (guarded by `test_pins.py` + full battery + host-smoke; its imports surface is inside P1's review). P9 IS the final closure review (Part 2 P9: full-branch opencode review, ≤3 fix rounds, DoD checklist).
- **Loop:** implement phase green → `review` command → triage findings into BLOCKING (contract/behavior/test-integrity) vs EDITORIAL (naming/comments) → fix blocking (editorial optional) commit `review: P<n> fixes round r<k>` → battery re-run → reviewer re-check. **Max 3 rounds;** after round 3 with open blocking findings: phase does NOT close, STATUS gets `blocking_issues:` entry, work stops for operator review. Verdicts + fix hashes recorded in STATUS `review_log` (mandatory phases show `r<k>` verdict lines in the §8.1 example format).

### 9.3 Proof-of-work deliverables (task DoD #4; storage root `/opt/data/results/2026-11-24/pai2-migration/pow/`)
- **Web UI screenshots — captured at P7 and refreshed at P9.** P7 ships the screenshot-ready e2e (login → chat → task board → answer form → fallback banner div, DOM-asserted); capture the PNG set at P7 under `pow/screenshots/` (P7 set marked `runner=executor` post-flip). P9 re-captures the same flow as the final-state proof (docs updated, smoke scripts verified). Capture commands documented in the plan doc per P7 test 7.
- **Example API calls — curl transcripts at P6 and P7**, saved verbatim (command + full response, incl. SSE stream head) under `pow/api/<name>.txt`: `healthz`, `login`, `chat` + `chat/stream` head, `sessions` list/detail, `tasks` create + `task/{id}` status, `tasks/answer/{task_id}` driving a full deferred-gate park→answer round-trip (P6: via direct task drop against the executor adapter path), schedule CRUD, handoff. Each transcript carries a header line with commit sha + `tasks.runner` value.
- **Task execution logs:** end-to-end folder-queue runs captured under `pow/tasks/` — P6: gate park + timer contract + restart-survival run (transcript + events.jsonl + gates.jsonl excerpts); P7: `runner=executor` task (`task.yaml` drop → analysis hint → executor run → `delegate_run(opencode)` tool event in transcript → `result.md`); P8: scheduler `spawn_agent` → inbox → single-runner pickup log. Each artifact set references the commit sha and is pointed to from STATUS `pow:` rows.
- Final DoD-4 proof = the three artifact sets + their STATUS pointers, reviewed in P9.

### 9.4 Gate discipline — no phase merger while tests red; DoD mapping
- **Red discipline:** the ONLY allowed red state is the phase-opening failing-tests commit (TDD rule). A phase "closes" only when, at the closing commit: the phase's pytest subset green AND `pytest -m "not smoke"` (full battery) green AND `ruff check src/bob-harness/bob_harness src/bob-harness/tests` clean AND the closure is recorded in STATUS with the closing sha. No phase may be declared done, and the branch may not be merged to any mainline, while any subset, battery, or review-blocking finding is red/open.
- **Rollback lever:** the P7 flip is the only behavior change; revert = `BOB_HARNESS_TASKS_RUNNER=analyzed` (compose-documented, P7 test 2). All other phases are additive/merge-safe ✅ per Part 2.
- **Definition of done — mapping to task file DoD items 1–5:**

| Task DoD | Delivered by (phases) | Evidence |
|---|---|---|
| 1. Harness on Pydantic AI v2 per v2 principles (capabilities) | P0 (pins+lock), P1 (router v2), P2–P5 (executor + Memory/Skills capabilities), P6 (deferred-tool gate), P8 (CapabilityEvent bridge) | `test_pins.py`, `test_router_v2_port.py`, `test_adapter_executor.py`, `test_memory_store_v2.py`, `test_skills_v2_mount.py`, `test_gate_deferred.py`, `test_capability_events_bridge.py`; §3.2 manifest rendered in docs (P9) |
| 2. Hermes + OpenCode as subagents for delegated work | P3 (`delegate_run` custom capability over the REAL subprocess adapters, D1) + P7 (executor default makes delegation the live path) | `test_delegation_capability.py` (incl. `test_no_subagents_entries_for_clis`), PoW task logs with `delegate_run` tool events, argv/child-env snapshot tests green throughout |
| 3. Web UI (chat) + Discord bot preserved, same behavior | P6 (gate FSM/API/timer parity), P7 (gateway/discord call-sites on v2 surface; SSE frames, threads, home channel, endpoints unchanged) | `test_gateway_v2_surface.py`, `test_gateway_app.py`, `test_discord_bot.py`, `test_e2e_scenarios.py`; screenshot set (fallback banner + chat + board); curl transcripts |
| 4. Working and tested: UI screenshots, example API calls, task executions as proof of work | §9.3 captures at P6–P9 | `pow/screenshots/`, `pow/api/`, `pow/tasks/` + STATUS `pow:` pointers |
| 5. Documentation updated | P9 (`docs/OVERVIEW.md`, `ARCHITECTURE.md`, `CONFIGURATION.md`, plan doc finalized; smoke runbooks) | `test_docs_smoke_contract.py` (docs record pins, manifest, `harness:` keys incl. `tasks.runner` + `BOB_HARNESS_TASKS_RUNNER`) |

## Appendix A: exact command crib

All pytest/ruff invocations run from `src/bob-harness/` (pytest `asyncio_mode=auto`, `pythonpath=[.]`); `pytest -m smoke` requires `BOB_HARNESS_SMOKE_*` env and is run only via the smoke scripts, never inside the phase battery.

**Phase green gates (subset → then, ALWAYS, full battery + ruff):**

| Phase | Subset (run first, expects the recorded state) |
|---|---|
| P0 | `pytest -q tests/test_pins.py` |
| P1 | `pytest -q tests/test_router.py tests/test_router_provider_down.py tests/test_router_v2_port.py` |
| P2 | `pytest -q tests/test_adapter_executor.py tests/test_config.py` |
| P3 | `pytest -q tests/test_delegation_capability.py tests/test_adapter_executor.py` |
| P4 | `pytest -q tests/test_memory.py tests/test_memory_store_v2.py tests/test_router.py` |
| P5 | `pytest -q tests/test_skills.py tests/test_skills_v2_mount.py tests/test_supervisor.py` |
| P6 | `pytest -q tests/test_gate_deferred.py tests/test_taskloop.py tests/test_replay.py tests/test_tasks.py` |
| P7 | `pytest -q tests/test_gateway_v2_surface.py tests/test_gateway_app.py tests/test_gateway_ui.py tests/test_discord_bot.py` |
| P8 | `pytest -q tests/test_capability_events_bridge.py tests/test_bus.py tests/test_scheduler.py tests/test_scheduler_battery.py tests/test_taskloop_schedule_demand.py` |
| P9 | `pytest -q tests/test_docs_smoke_contract.py` then (host) `bash scripts/dev-incontainer-tests.sh` |

Closing gate for EVERY phase (run after the subset is green):
```
pytest -m "not smoke"
ruff check src/bob-harness/bob_harness src/bob-harness/tests
```
(repo root; then commit green + append STATUS.md block).

**Lock regen sequence (P0, exactly once; OQ8 policy):**
```
cd src/bob-harness
uv venv /tmp/pai2-lockregen --python 3.13
VIRTUAL_ENV=/tmp/pai2-lockregen uv pip install -r requirements.txt
VIRTUAL_ENV=/tmp/pai2-lockregen uv pip freeze > requirements.lock
grep -E 'pydantic-ai==2.73.0|pydantic-ai-slim==2.73.0|pydantic-ai-harness==0.73.0|json-repair' requirements.lock
rm -rf /tmp/pai2-lockregen
```
Freeze policy: runtime deps ONLY from `requirements.txt` (cap flipped to `>=2,<3` + `pydantic-ai-harness[coder]==0.73.0`); no dev tools ever installed into the lock venv BEFORE the freeze (`requirements-dev.txt` unchanged, never merged into the lock); exact resolved transitive pins frozen as-is (fastapi/starlette may drift within caps); single regen session → immediate commit → full offline battery re-run; never regen mid-phase.

**Container/host smoke:**
```
docker build -f dockerfiles/Dockerfile.hermes -t bob-harness:pai2 .   # manual build; host-smoke wraps this
bash scripts/host-smoke.sh                                            # boots image, waits 127.0.0.1:8181, asserts import + /healthz (+ P7: executor task pickup)
bash scripts/dev-incontainer-tests.sh                                 # pytest+ruff inside the container (P0/P9 verification)
```

**Review invocation (§9.2; review round r<k> of phase P<n>):**
```
opencode run --agent code-reviewer "Review commits <sha-open>..<sha-green> on branch custom-harness-poc (task 10 pydantic-ai-v2 migration, phase P<n>). Contract: part 2 phase P<n> goal/exit criteria + D<id> rationale in docs/plans/10-pydantic-ai-v2-migration-plan.md. Classify findings BLOCKING vs EDITORIAL; check tests are failing-first-derived, battery-affecting, registry/format contracts intact."
```
(then per §9.2 loop; ≤3 rounds; verdicts into STATUS `review_log`).

**STATUS append one-liner (after every commit / review round):**
```
printf '%s | %s | %s\n' "$(date -u +%FT%TZ)" "P<n> <state>" "<sha> <one-line note>" >> /opt/data/results/2026-11-24/pai2-migration/STATUS.md
```
(plus the structured §8.1 block edit; the one-liner is the checkpoint trail used by 20–30 min scheduled checks).

**PoW capture quick-set (P6/P7):**
```
curl -s http://127.0.0.1:8181/healthz | tee pow/api/healthz.txt
curl -s -X POST http://127.0.0.1:8181/api/login -H 'Content-Type: application/json' -d '{"api_key":"'"$BOB_HARNESS_API_KEY"'"}' | tee pow/api/login.txt
curl -s -N -X POST http://127.0.0.1:8181/api/chat -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' -d '{"message":"hello"}' | tee pow/api/chat.txt
curl -s -X POST http://127.0.0.1:8181/api/tasks -H "Authorization: Bearer <token>" -d @pow/tasks/task-01.yaml ... | tee pow/api/tasks_create.txt
curl -s -X POST http://127.0.0.1:8181/api/tasks/answer/<task_id> -H "Authorization: Bearer <token>" -d '{"answer":"<user answer>"}' | tee pow/api/tasks_answer.txt
```
(header each file with commit sha + `tasks.runner` value; screenshots via the P7 e2e flow into `pow/screenshots/`).

## Appendix B: RESUME prompt template

Paste verbatim as the first message of every coder session after a token reset:

```
You are the bob-harness migration coder agent (Task 10: Pydantic AI v2 migration) resuming after a token-budget reset. Workdir: /opt/data/projects/bob-the-agent. Branch: custom-harness-poc. Authoritative sources: docs/plans/10-pydantic-ai-v2-migration-plan.md (Parts 1-3; D1-D7 + OQ1-OQ8 are BINDING — do not re-litigate), fact sheet /tmp/pai2-plan-context.md, phase plan Part 2 (P0-P9).

FIRST ACTIONS, in order:
1. Read /opt/data/results/2026-11-24/pai2-migration/STATUS.md completely: current_phase + state, per-phase commit hashes, next_steps (verbatim), blocking_issues, review_log, pow pointers.
2. Verify repo: git log --oneline -15 ; git status. HEAD must match STATUS recorded hashes; dirty tree must be resolved (stash or finalize) before any new work — never silently mix it into a phase commit.
3. Run this phase's pytest subset (Appendix A of the plan, from src/bob-harness/): expect the exact red/green fingerprint recorded in STATUS. If reality doesn't match STATUS, STOP and record a blocking issue instead of improvising.
4. Continue exactly at STATUS next_steps: TDD failing-tests-first -> implement -> subset green -> full battery `pytest -m "not smoke"` -> `ruff check src/bob-harness/bob_harness src/bob-harness/tests` -> commit (Co-Authored-By: mjaniszew) -> update STATUS.md.

HARD RULES:
- NEVER push, NEVER merge, never amend other authors' commits; commit often (red-open, green-close, review fixes).
- Phase closes ONLY on green (subset + battery + ruff) at the closing sha recorded in STATUS; a red state exists only at the phase-opening TDD commit; no phase merger while anything is red.
- Reviews: P1/P3/P4/P5/P6/P7 mandatory (P2/P8 optional per Part 3 §9.2 triggers); max 3 fix rounds; open blockers after round 3 -> STATUS blocking_issues + stop.
- Token budget 5h: scheduled self-checks every 20-30 minutes (append checkpoint line to STATUS); if <40 min remain before the reset boundary do NOT open a new failing-test cycle — land the current step green (or commit the red-open state) and update STATUS.
- Never touch .env/env.* files; never regen requirements.lock except at P0 per OQ8.
- New session quality bar: behave as if the plan doc is your memory; STATUS.md + Appendix B are the resume mechanism. Keep answers/actions minimal and verifiable.

Current work: <paste current_phase/state + first two next_steps lines from STATUS.md here>
```

<!-- append-anchor: P9 closure appendix -->

## Appendix C: Closure appendix (phases P0-P9, commit chain + review verdicts)

Final state at closure: branch `custom-harness-poc` (NEVER pushed; origin
`origin/custom-harness-poc` remains at `7200ad3`), full battery
`pytest -m "not smoke"` green (0 failed / 3 deselected), ruff repo-wide
in-package findings at the pre-existing baseline (17), and the P0-P9 plan
contracts closed per `/opt/data/results/2026-11-24/pai2-migration/STATUS.md`
(state source of truth: commit shas, gate outputs, fingerprint details).

### Phase → commit chain (f0d361e..HEAD)

| Phase | Test (RED) commit | Impl (GREEN) commit | Subject of the green commit |
|---|---|---|---|
| PLAN | — | `f0d361e` | docs: pydantic-ai v2 migration implementation plan (task 10, plan-only) |
| P0 | `c92c9ee` | `8bcf58f` | feat(deps): pin pydantic-ai 2.73.0 + pydantic-ai-harness[coder] 0.73.0, silence v2 import banner |
| P1 | `add4721` | `4e657ab` | feat(router): port RouterBrain to pydantic-ai 2.x Agents (capability-free, D7) |
| P2 | `f02cf10` | `3fc22d2` | feat(executor): adapter wrapping pydantic-ai v2 Agent behind AgentRun protocol (P2) |
| P3 | `72e721f` | `25e6350` | feat(delegation): delegate_run capability - hermes profiles + opencode as pydantic-ai v2 delegation targets (P3, D1) |
| P4 | `aaa5683` | `2ff3cbb` (+ accuracy follow-up `bc1bab3`) | feat(memory): MemoryEngine over harness FileMemoryStore, same plain files + budgets (D4) |
| P5 | `c335e9c` | `95574ec` | feat(executor): mount harness Skills capability on existing skill folders (D5; INDEX.md engine-owned) |
| P6 | `0290d1c` | `610e4a4` | feat(gate): WAITING_INPUT on the v2 deferred-tool flow with persisted resume seed; FSM/timers/API unchanged (D3) |
| P7 | `c500eea` | `bfdec97` | feat(gateway): v2 agent calls + executor runner default (flag tasks.runner=analyzed\|executor) + screenshot-ready e2e |
| P8 | `6460310` | `cbfd249` | feat(bus): CapabilityEvent bridge topics for ask_user; scheduler task spawns unchanged (req 8) |
| P9 | `ae7d93d` | (green commit — see STATUS) | docs: v2 architecture/configuration sections + smoke runbooks (final review) |

All commits co-authored `Co-Authored-By: mjaniszew` (name-only trailer, per
§9.1); several phases needed watchdog recovery attempts after coder budget
deaths — the recorded commit chain is the merged outcome of those attempts
(attempt lineage and salvage details live in STATUS.md checkpoint_trail, not
repeated here).

### Review verdicts (opencode code-reviewer, per §9.2)

| Phase | Verdict | Findings |
|---|---|---|
| P0 | APPROVE (opencode.db session `ses_df1059a3effeZ1lPurTXzlAZXL`) | 0 blocker/major; 6 notes (all pre-existing/explained) |
| P1 | not separately recorded in STATUS (mandatory per §9.2) | — gap acknowledged: no review session exists in opencode.db; P1's router-impl surface was exercised by every later phase's battery + 4 mandatory reviews that include router call-sites |
| P2 | none (optional per §9.2; triggers — battery anomaly / LOC>25% / out-of-list files — never fired) | — |
| P3 | not separately recorded in STATUS (mandatory per §9.2) | — gap acknowledged: no review session exists in opencode.db; the D1 delegation surface was independently re-verified by the P7 review (relay/registry paths) |
| P4 | APPROVE r1 | 0 blocking / 2 editorial (executor docstring workspace_root drift; two store instances over one shared root — both accepted) |
| P5 | APPROVE r1 | 0 blocking / 0 editorial |
| P6 | APPROVE r1 | 0 blocking / 4 editorial (no fix round needed) |
| P7 | APPROVE r1 | 0 blocking / 6 editorial (incl. capture-doc command correction → owned and fixed by P9, §Appendix C note below); one divergent BLOCK-verdict run existed whose sole blocking claim was that same docs-only editorial item — resolved by the severity taxonomy (docs-only findings are never blocking when tests pin no more than existence) |
| P8 | none (optional per §9.2; all triggers clear) | — |
| P9 | THIS closing review | ≤3 fix rounds per §9.2; verdict recorded in STATUS `review_log` after the run |

Editorial carry-over folded into P9 (the P7-review capture-doc correction):
`src/bob-harness/docs/taking-ux-captures.md` manual-capture commands corrected —
compose service `gateway` → `agent` (compose service list, compose.yaml:56),
base URL/port `8420` → `8181` (published loopback port, compose.yaml:63), login
body key `key` → `api_key` (gateway `POST /api/login` handler +
`ui.py` inline JS). All three verified against the real files before the edit.

### DoD status (plan §9.4 table)

1. Harness on Pydantic AI v2 per v2 principles (capabilities) — DONE (P0-P5,
   P6, P8) — evidence: `test_pins.py`, `test_router_v2_port.py`,
   `test_adapter_executor.py`, `test_memory_store_v2.py`,
   `test_skills_v2_mount.py`, `test_gate_deferred.py`,
   `test_capability_events_bridge.py`; the §3.2 capability manifest is rendered
   in `docs/ARCHITECTURE.md` ("Capability manifest — executor agent").
2. Hermes + OpenCode as subagents for delegated work — DONE (P3 + P7) —
   evidence: `test_delegation_capability.py` (incl.
   `test_no_subagents_entries_for_clis`), PoW task logs with `delegate_run`
   tool events, argv/child-env snapshot tests green throughout.
3. Web UI (chat) + Discord bot preserved, same behavior — DONE (P6 + P7) —
   evidence: `test_gateway_v2_surface.py`, `test_gateway_app.py`,
   `test_discord_bot.py`, `test_e2e_scenarios.py`; screenshot set + curl
   transcripts per §9.3 (pointer: STATUS `pow:` rows).
4. Working and tested: UI screenshots, example API calls, task executions as
   proof of work — captures were scoped to P6/P7 (plan §9.3); STATUS `pow:`
   section carries the pointers that exist — GAP acknowledged: no
   `pow/screenshots`, `pow/api`, or `pow/tasks` artifact sets are on disk in
   this session's results tree; the capture runbooks are the corrected
   `src/bob-harness/docs/taking-ux-captures.md` + `scripts/host-smoke.sh`
   checks. Acceptance of DoD-4 evidence remains an explicit operator action
   (never pushed, no live stack in this session).
5. Documentation updated — DONE (P9, this phase) — evidence:
   `tests/test_docs_smoke_contract.py` (docs record pins, manifest, `harness:`
   keys incl. `tasks.runner` + `BOB_HARNESS_TASKS_RUNNER`), this appendix, the
   corrected capture runbook, and the updated smoke scripts.

<!-- append-anchor: P9 closure appendix end -->
