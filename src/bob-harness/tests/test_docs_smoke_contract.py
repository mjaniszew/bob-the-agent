"""Failing-first P9 battery pinning the docs + smoke-script v2 contract.

Plan: ``docs/plans/10-pydantic-ai-v2-migration-plan.md`` Part 2 section
``### P9 — Docs + smoke scripts + final review`` (5 test names used verbatim
below) plus the Appendix A/§9.4 DoD mapping. Review lesson: docs and script
tests pin FILE EXISTENCE and ANCHOR STRINGS only — never deep structure —
and every pin here targets content this phase itself writes (docs v2
sections, smoke-script needle blocks), so a red fingerprint means the impl
is missing, not that a pin was weakened. All existing pins elsewhere in the
battery (e.g. ``test_gateway_v2_surface.py`` capture-doc pins) must stay
byte-green: docs edits are ADDITIVE sections, never contract-string deletes.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# pydantic-ai v2 family + harness extra + json-repair, exactly as frozen in
# src/bob-harness/requirements.lock at P0 (plan P9 test 1: docs must record
# the pydantic-ai 2.73.0 + pydantic-ai-harness 0.73.0 pins).
V2_PIN_ANCHORS = (
    "pydantic-ai==2.73.0",
    "pydantic-ai-slim==2.73.0",
    "pydantic-graph==2.73.0",
    "pydantic-evals==2.73.0",
    "pydantic-ai-harness[coder]==0.73.0",
    "json-repair==0.64.9",
    "pydantic==2.14.4",
    "pydantic-core==2.50.5",
)

# The harness: config group keys (bob_harness/config.py HarnessHarnessConfig
# + _FLAT_ENV_BINDINGS, plan §3.4 / P9 test 3).
HARNESS_GROUP_KEYS = (
    "harness.memory.enabled",
    "harness.memory.dir",
    "harness.memory.workspace_root",
    "harness.memory.agent_name",
    "harness.memory.inject",
    "harness.memory.max_tokens",
    "harness.skills.enabled",
    "harness.skills.include",
    "harness.skills.exclude",
    "harness.skills.read_files",
    "harness.skills.missing_directories",
    "harness.ask_user.enabled",
    "harness.ask_user.id",
    "harness.ask_user.answerer",
    "harness.ask_user.timeout_s",
    "harness.subagents.enabled",
    "harness.subagents.max_depth",
    "harness.delegation.enabled",
    "harness.delegation.tool_name",
)

# Flat BOB_HARNESS_* env bindings documented in the flat env table (P9
# test 3; list-valued YAML-only keys deliberately have no flat binding).
HARNESS_FLAT_ENV_VARS = (
    "BOB_HARNESS_MEMORY_ENABLED",
    "BOB_HARNESS_MEMORY_DIR",
    "BOB_HARNESS_MEMORY_WORKSPACE",
    "BOB_HARNESS_MEMORY_AGENT_NAME",
    "BOB_HARNESS_MEMORY_INJECT",
    "BOB_HARNESS_MEMORY_MAX_TOKENS",
    "BOB_HARNESS_SKILLS_ENABLED",
    "BOB_HARNESS_SKILLS_READ_FILES",
    "BOB_HARNESS_SKILLS_MISSING",
    "BOB_HARNESS_ASK_USER_ENABLED",
    "BOB_HARNESS_ASK_USER_ANSWERER",
    "BOB_HARNESS_ASK_USER_TIMEOUT_S",
    "BOB_HARNESS_SUBAGENTS_ENABLED",
    "BOB_HARNESS_SUBAGENTS_MAX_DEPTH",
    "BOB_HARNESS_DELEGATION_ENABLED",
    "BOB_HARNESS_TASKS_RUNNER",
)


def _read(repo_rel: str) -> str:
    path = REPO_ROOT / repo_rel
    assert path.is_file(), f"expected file missing: {repo_rel}"
    return path.read_text(encoding="utf-8")


def test_docs_record_v2_pins() -> None:
    """P9 test 1: OVERVIEW + ARCHITECTURE + CONFIGURATION each record the
    pydantic-ai 2.73.0 + pydantic-ai-harness 0.73.0 dependency pins (plus the
    rest of the frozen v2 lock family) in their v2 migration sections."""
    overview = _read("docs/OVERVIEW.md")
    architecture = _read("docs/ARCHITECTURE.md")
    configuration = _read("docs/CONFIGURATION.md")
    for doc_name, text in (
        ("docs/OVERVIEW.md", overview),
        ("docs/ARCHITECTURE.md", architecture),
        ("docs/CONFIGURATION.md", configuration),
    ):
        for anchor in (
            "pydantic-ai==2.73.0",
            "pydantic-ai-harness[coder]==0.73.0",
            "json-repair==0.64.9",
            "pydantic==2.14.4",
            "pydantic-core==2.50.5",
        ):
            assert anchor in text, f"{doc_name} missing v2 pin anchor: {anchor}"
    for anchor in V2_PIN_ANCHORS:
        assert anchor in architecture, f"ARCHITECTURE.md missing v2 pin: {anchor}"


def test_architecture_documents_capability_manifest() -> None:
    """P9 test 2: ARCHITECTURE renders the D1-D7 decision rationale and the
    §3.2 capability manifest (existence + anchor strings only)."""
    architecture = _read("docs/ARCHITECTURE.md")
    section_anchors = (
        "Pydantic AI v2 Architecture",
        "Design decisions D1-D7",
        # D1-D7 rationale, one anchor per decision (plan §2 titles).
        "D1 — Delegation via a custom capability (toolset), NOT SubAgents entries",
        "D2 — executor adapter in the existing registry; taskloop remains sole invoker",
        "D3 — WAITING_INPUT gate on deferred tool calls; persisted resume seed",
        "D4 — One shared memory store; plain-file format + budgets preserved",
        "D5 — Harness Skills capability on existing folders; SkillsEngine keeps INDEX.md",
        "D6 — Harness coder capabilities NOT adopted",
        "D7 — Router stays capability-free",
        # §3.2 capability manifest, executor agent.
        "Capability manifest — executor agent",
        "delegate_run",
        "FileMemoryStore",
        "LocalWorkspaceBackend",
        "PYDANTIC_AI_NO_BANNER",
        # P8 CapabilityEvent bridge topics.
        "harness.ask_user.requested",
        "harness.ask_user.answered",
        # D6 explicitly-NOT-mounted list.
        "Coder",
        "FileSystem",
        "Shell",
        "DockerContainer",
        "ModelRouter",
    )
    for anchor in section_anchors:
        assert anchor in architecture, f"ARCHITECTURE.md missing anchor: {anchor}"


def test_configuration_documents_harness_group() -> None:
    """P9 test 3: CONFIGURATION documents the ``harness:`` config group keys
    (incl. the executor/memory/skills/ask_user/subagents/delegation surfaces),
    the flat BOB_HARNESS_* env table, and tasks.runner (analyzed|executor,
    executor default since P7, rollback env BOB_HARNESS_TASKS_RUNNER)."""
    configuration = _read("docs/CONFIGURATION.md")
    anchors = (
        "Harness Capability Configuration",
        *HARNESS_GROUP_KEYS,
        *HARNESS_FLAT_ENV_VARS,
        # tasks.runner flag + defaults + rollback lever (plan P9 test 3).
        "tasks.runner",
        "analyzed",
        "executor default since P7",
        "BOB_HARNESS_TASKS_RUNNER=analyzed",
    )
    for anchor in anchors:
        assert anchor in configuration, f"CONFIGURATION.md missing anchor: {anchor}"


def test_host_smoke_v2_checks() -> None:
    """P9 test 4: scripts/host-smoke.sh asserts the v2 contract needles —
    lock pins, banner quiet, /healthz, executor task pickup — keeping the
    P7 9-check structure (existence + anchor strings only)."""
    script = _read("scripts/host-smoke.sh")
    structural_anchors = (
        # P7-era checks preserved (banner quiet + healthz + executor pickup).
        "PYDANTIC_AI_NO_BANNER",
        "/healthz",
        "runner=executor",
        "executor-backed",
        "starting bob-harness (python -m bob_harness.supervisor)",
        "ALL CHECKS PASSED",
        # P9 addition: v2 dependency lock pins asserted host-side.
        *V2_PIN_ANCHORS,
    )
    for anchor in structural_anchors:
        assert anchor in script, f"scripts/host-smoke.sh missing anchor: {anchor}"
    # P7 review precedent: the smoke script must never need (or carry)
    # chown/chmod/sudo — grep the script file (target is the script, not
    # this test's own source).
    for forbidden in ("chown", "sudo"):
        assert forbidden not in script, (
            f"scripts/host-smoke.sh must not use {forbidden}"
        )


def test_dev_incontainer_script_contract() -> None:
    """P9 test 5: scripts/dev-incontainer-tests.sh keeps the canonical
    contract — cd into src/bob-harness and run pytest -m "not smoke" — and
    notes the P9 docs+smoke contract suite (existence + anchor strings)."""
    script = _read("scripts/dev-incontainer-tests.sh")
    for anchor in (
        'cd "$repo_root/src/bob-harness"',
        'pytest -m "not smoke"',
        # P9 note anchor (script update of this phase).
        "tests/test_docs_smoke_contract.py",
    ):
        assert anchor in script, f"scripts/dev-incontainer-tests.sh missing anchor: {anchor}"
