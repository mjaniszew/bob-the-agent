"""The delegate_run delegation capability (plan task 10, P3 / decision D1).

Exposes the hermes and opencode subprocess adapters to the executor agent as
delegation targets through a custom ``AbstractCapability`` toolset. This is
the D1 decision implemented: hermes/opencode remain REAL subprocess adapters
(same registry instances the gateway uses); NO harness ``SubAgents`` entry is
created for either CLI — ``SubAgent.agent`` requires an in-process
``AbstractAgent`` message graph, which would fake the CLIs' protocol state.

Plan §3.2 surface (``delegate_run`` custom):

    custom ``AbstractCapability``: ``get_toolset`` → ``delegate_run(agent:
    'hermes'|'opencode', prompt, profile?, workdir?, resume_ref?)`` whose
    impl calls the registered adapter's ``run(spec)`` and re-streams
    ``StreamEvent``s into the executor run (``wrap_run_event_stream``).

Dispatch-contract additions honored here (beyond the plan P3 test list):

- **Target naming**: ``hermes`` (default profile via ``_normalize_profile``
  rules), ``hermes-simple``/``hermes-researcher`` (explicit profile) and
  ``opencode``. An unknown target returns an error result telling the model
  the valid names — no adapter is called.
- **RunSpec translation**: the tool call arguments become
  :class:`~bob_harness.adapters.base.RunSpec` fields (prompt, profile,
  workdir, resume_ref; ``oneway=False``, ``files=[]``); ``spec.agent`` stays
  unset (the child CLI agent is the adapter's config default).
- **Result capture**: the delegation result is a structured mapping
  (``status`` final|failed|question|error, ``final``, ``error``,
  ``question``, ``session_ref``, ``agent``) returned as a ``ToolReturn``;
  child events are relayed into the parent run as :class:`DelegateRunEvent`
  capability events (probe-verified live: capability tools must emit
  ``CapabilityEvent`` subclasses, not application ``CustomEvent``s).
- **Question pass-back (P3 scope)**: a child ``system`` event carrying the
  WAITING_INPUT parked-question shape ``{"gate_question": "..."}`` (the same
  bytes ``taskloop._gate_question`` consumes today) is detected and surfaced
  in the structured result (``status='question'``); the full deferred-gate
  wiring (park + resume) is P6.
- **Ledger**: every delegation appends one JSONL row with
  ``kind == 'delegation'`` at ``<storage.data_dir>/delegations.jsonl``
  (mirrors the per-run ``runs_ledger.jsonl`` pattern; the tasks/session
  event registries are closed sets and stay untouched).
- **Budgets**: the child prompt is capped (``DELEGATION_PROMPT_CHAR_CAP``)
  and the event relay is bounded (``DELEGATION_EVENT_CHAR_CAP`` per event
  text, ``DELEGATION_MAX_EVENTS`` events per delegation) so a runaway child
  cannot flood the parent transcript.
- **Child env**: untouched — the adapters keep building child envs via
  ``base.build_child_env`` (the ``BOB_HARNESS_*`` deny prefix is enforced
  there). This module builds NO child env (asserted by tests).

No side effects on import (no ``__main__`` block; only definitions).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import CapabilityEvent, ToolReturn
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from bob_harness.adapters import RunSpec, StreamEvent
from bob_harness.persist import JsonlWriter

__all__ = [
    "DELEGATION_EVENT_CHAR_CAP",
    "DELEGATION_LEDGER_FILE_NAME",
    "DELEGATION_MAX_EVENTS",
    "DELEGATION_PROMPT_CHAR_CAP",
    "DelegateRunEvent",
    "DelegationCapability",
    "extract_gate_question",
]

# ---------------------------------------------------------------------------
# Budget constants (relay guard rails)
# ---------------------------------------------------------------------------

DELEGATION_PROMPT_CHAR_CAP = 12_000
"""Child prompt cap: an oversized delegate prompt is truncated to this."""

DELEGATION_EVENT_CHAR_CAP = 2_000
"""Per-event text cap when relaying child chunk text to the parent."""

DELEGATION_MAX_EVENTS = 500
"""Maximum relayed child events per delegation (flood guard)."""

DELEGATION_LEDGER_FILE_NAME = "delegations.jsonl"
"""Ledger file name under ``cfg.storage.data_dir`` (kind='delegation' rows)."""

_HERMES_TARGET_RE = re.compile(r"^hermes(?:-([A-Za-z0-9_-]+))?$")

_VALID_PROFILES: tuple[str, ...] = ("simple", "researcher")
"""Repo reconciliation with router.py: task 22 — only provisioned profiles."""


def extract_gate_question(raw: str) -> str | None:
    """The WAITING_INPUT parked-question payload from a child system event.

    Parses the fake-adapter/PoC gate shape ``{"gate_question": "..."}`` — the
    exact bytes :meth:`bob_harness.taskloop.TaskLoop._gate_question` consumes
    on the task path. ``None`` when the raw line is not that shape.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict):
        question = data.get("gate_question")
        if isinstance(question, str) and question.strip():
            return question
    return None


def _truncate(text: str, cap: int) -> str:
    """Cap a string to EXACTLY ``min(len(text), cap)`` chars.

    Head-keep only. The honest truncation marker is computed FIRST and the
    head sliced so head + marker lands exactly on ``cap`` when truncation
    happens (test-pinned: the capped child prompt length == the cap). The
    short-input path is unchanged (``len(text) <= cap`` returns text as-is).
    """
    if len(text) <= cap:
        return text
    marker = f"…[truncated {len(text) - cap} chars]"
    head_len = cap - len(marker)
    if head_len >= 0:
        return text[:head_len] + marker
    return marker[:cap]


@dataclass(kw_only=True)
class DelegateRunEvent(CapabilityEvent, namespace="bob_harness.delegation"):
    """One relayed child StreamEvent inside the delegating parent run.

    ``child_kind`` mirrors the closed adapter StreamEvent set (``chunk`` /
    ``final`` / ``failed`` / ``tool`` / ``started`` / ``system``); ``target``
    names the delegation target (``hermes-simple`` / … / ``opencode``).
    """

    child_kind: str
    target: str | None = None
    text: str | None = None
    error: str | None = None
    session_ref: str | None = None


@dataclass(kw_only=True)
class DelegationCapability(AbstractCapability):
    """Expose hermes/opencode subprocess adapters as delegation tools (D1).

    ``registry`` maps adapter names to :class:`~bob_harness.adapters.base
    .AgentRun` objects — the SAME registry instances the gateway/taskloop
    resolve via ``bob_harness.adapters.get``. ``cfg`` is the executor's
    :class:`~bob_harness.config.HarnessConfig` (ledger dir lives at
    ``cfg.storage.data_dir``); ``None`` is allowed for direct toolset-only
    tests (no ledger rows are written until a delegation runs with a cfg).

    The class attribute ``_hermes_default_profile`` names the profile a bare
    ``hermes`` target gets (``None`` → the child adapter resolves its config
    ``default_profile`` itself, exactly like a router-dispatched run).
    """

    registry: dict[str, Any] = field(default_factory=dict)
    cfg: Any | None = None
    id: str = "delegation"
    instruction: str = (
        "Delegate work with delegate_run: agent='hermes' (general/research "
        "chat; or hermes-simple/hermes-researcher for an explicit profile), "
        "agent='opencode' (software engineering; needs a nested project "
        "workdir). One delegation per decision; act on the returned result."
    )

    _hermes_default_profile: str | None = None

    def get_instructions(self) -> str | None:
        """Tell the model the delegation surface exists and what it maps to."""
        return self.instruction

    def get_toolset(self) -> FunctionToolset:
        """The ``delegate_run`` tool (D1: custom capability, NOT SubAgents)."""
        toolset = FunctionToolset()
        toolset.add_function(
            self._delegate_run, takes_ctx=True, name="delegate_run"
        )
        return toolset

    # -- target/profile resolution (_normalize_profile-equivalent rules) ----

    def _resolve_target(
        self, agent: str, profile: str | None
    ) -> tuple[str | None, str | None]:
        """Map the tool ``agent`` argument to (adapter_name, child_profile).

        ``hermes``/``hermes-simple``/``hermes-researcher`` → the ``hermes``
        adapter with the explicit suffix profile (or the capability default,
        else the adapter's config default when ``None``); an explicit valid
        ``profile`` argument wins over the target suffix. Any invalid profile
        (not in ``_VALID_PROFILES``) is dropped to ``None`` — the same keep-
        only-provisioned rule as ``router._normalize_profile``. An unknown
        target name resolves to ``(None, None)`` (error result upstream).
        """
        match = _HERMES_TARGET_RE.match(agent)
        if match is not None:
            suffix = match.group(1)
            explicit = profile.strip() if profile else None
            chosen = explicit if explicit in _VALID_PROFILES else None
            if chosen is None and suffix in _VALID_PROFILES:
                chosen = suffix
            if chosen is None and suffix is None:
                chosen = self._hermes_default_profile
            return "hermes", chosen
        if agent == "opencode":
            # opencode has no profile dimension; an explicit profile on an
            # opencode delegation is ignored (the adapter takes spec.profile
            # as a hermes-only field and never emits it).
            return "opencode", None
        return None, None

    # -- tool impl ---------------------------------------------------------

    async def _delegate_run(
        self,
        ctx: RunContext,
        agent: str,
        prompt: str,
        profile: str | None = None,
        workdir: str | None = None,
        resume_ref: str | None = None,
    ) -> Any:
        """Run one delegated child run on the registered subprocess adapter.

        Args:
            agent: target adapter — ``hermes`` (adapter default profile),
                ``hermes-simple``/``hermes-researcher`` (explicit profile),
                or ``opencode`` (workdir required by the child adapter).
            prompt: the child prompt (capped to ``DELEGATION_PROMPT_CHAR_CAP``).
            profile: optional explicit hermes profile; invalid values are
                dropped (router ``_normalize_profile`` rules).
            workdir: optional child working directory (the opencode adapter
                validates it against ``agents.opencode.workdir_root`` itself).
            resume_ref: optional child session to resume (hermes ``--resume``
                / opencode ``--session``).

        Returns:
            A structured result mapping (``status`` final|failed|question|
            error, ``final``, ``error``, ``question``, ``session_ref``,
            ``agent``); child StreamEvents arrive as ``DelegateRunEvent``
            capability events while the tool runs.
        """
        started = datetime.now(UTC).isoformat(timespec="milliseconds")
        adapter_name, child_profile = self._resolve_target(agent, profile)
        if adapter_name is None:
            return {
                "status": "error",
                "error": (
                    f"unknown agent {agent!r}; valid targets: hermes, "
                    "hermes-simple, hermes-researcher, opencode"
                ),
                "agent": agent,
            }
        adapter = self.registry.get(adapter_name)
        if adapter is None:
            return {
                "status": "error",
                "error": f"agent {adapter_name!r} has no registered adapter",
                "agent": agent,
            }

        child_prompt = _truncate(prompt, DELEGATION_PROMPT_CHAR_CAP)
        spec = RunSpec(
            prompt=child_prompt,
            profile=child_profile,
            workdir=workdir,
            resume_ref=resume_ref,
            oneway=False,
            files=[],
        )

        final: str | None = None
        final_error: str | None = None
        question: str | None = None
        session_ref: str | None = None
        relayed = 0
        status = "failed"

        async def _relay(child_kind: str, event: StreamEvent) -> None:
            """Relay one child event into the parent run's event stream."""
            nonlocal relayed
            if relayed >= DELEGATION_MAX_EVENTS:
                return
            relayed += 1
            await ctx.emit(
                DelegateRunEvent(
                    child_kind=child_kind,
                    target=agent,
                    text=(
                        _truncate(event.text or "", DELEGATION_EVENT_CHAR_CAP)
                        if event.text is not None
                        else None
                    ),
                    error=event.error,
                    session_ref=event.session_ref,
                )
            )

        try:
            stream: AsyncIterator[StreamEvent] = self._stream(adapter, spec)
            async for event in stream:
                if event.kind == "chunk":
                    await _relay("chunk", event)
                elif event.kind == "tool":
                    await _relay("tool", event)
                elif event.kind == "system":
                    await _relay("system", event)
                    detected = extract_gate_question(event.raw or "")
                    if detected is not None:
                        # WAITING_INPUT shape detected (P3: surface the
                        # question in the result; P6 parks/resumes the gate).
                        question = detected
                        status = "question"
                elif event.kind == "failed":
                    await _relay("failed", event)
                    final_error = event.error or "child run failed"
                    status = "failed"
                    break  # terminal per the adapter contracts
                elif event.kind == "final":
                    final = event.final_text or ""
                    session_ref = event.session_ref
                    if status != "question":
                        status = "final"
                    await _relay("final", event)
                # 'started' events carry no parent-visible payload beyond
                # session_ref — relayed for symmetry with the closed-set rule.
        except Exception as exc:  # noqa: BLE001 - structured failure, never past the tool
            final_error = f"{type(exc).__name__}: {exc}"
            status = "error"

        result: dict[str, Any] = {"status": status, "agent": agent}
        if final is not None:
            result["final"] = final
        if final_error is not None:
            result["error"] = final_error
        if question is not None:
            result["question"] = question
        if session_ref is not None:
            result["session_ref"] = session_ref

        self._write_ledger(
            {
                "ts": started,
                "kind": "delegation",
                "agent": agent,
                "adapter": adapter_name,
                "profile": child_profile,
                "prompt_chars": len(child_prompt),
                "status": status,
                "session_ref": session_ref,
                "events_relayed": relayed,
            }
        )
        return ToolReturn(
            return_value=result,
            metadata={"delegation": agent, "status": status},
        )

    async def _stream(
        self, adapter: Any, spec: RunSpec
    ) -> AsyncIterator[StreamEvent]:
        """Iterate the adapter's run stream.

        ``adapter.run(spec)`` returns an async iterator directly (the
        :class:`~bob_harness.adapters.base.AgentRun` protocol — called
        WITHOUT awaiting).
        """
        stream = adapter.run(spec)
        async for event in stream:
            yield event

    # -- ledger -------------------------------------------------------------

    def _write_ledger(self, row: dict[str, Any]) -> None:
        """Append one kind='delegation' row (best effort; run keeps working
        when the store cannot be written — mirrors the adapter ledger stance
        of never masking a child result behind a bookkeeping failure)."""
        if self.cfg is None:
            return
        try:
            JsonlWriter(
                __import__("pathlib").Path(self.cfg.storage.data_dir)
                / DELEGATION_LEDGER_FILE_NAME
            ).append(row)
        except OSError:
            pass
