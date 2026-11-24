"""In-process executor agent adapter (plan task 10 §3.1/§3.2, phase P2).

:class:`ExecutorAdapter` implements the
:class:`~bob_harness.adapters.base.AgentRun` protocol WITHOUT spawning any
process (D2): the run executes in-process on a pydantic-ai 2.x
:class:`pydantic_ai.Agent` built per config by :func:`_build_agent`, with the
per-run workspace bound from ``spec.workdir`` via
``pydantic_ai.workspaces.LocalWorkspaceBackend`` (verified 2.73.0 surface:
``Agent(...)``, ``agent.iter(prompt, model=..., workspace=...)``,
``run.result.output``).

Stream contract (mirrors the hermes/opencode/fake adapters' closed-kind
StreamEvent shape):

- ``started`` first, carrying ``session_ref='exec-<name>'`` — the in-process
  run identity (no OS session exists).
- exactly ONE terminal event: ``final`` with ``final_text=<final output>`` on
  success, or ``failed`` whose error text includes the exception type and
  message when the model/run raises. Model errors are NEVER re-raised (F1/F2
  parity with the subprocess adapters: consumers treat a stream without a
  terminal event as a failure, so the adapter must always terminate its own
  stream).
- ``spec.prompt`` is the user prompt; ``spec.model`` (a pydantic-ai model
  name/instance) overrides the per-run model, falling back to the config
  router model (``cfg.router.base_url``/``model`` as an ``OpenAIChatModel``).

Stream scope note (OQ2, per the committed P2 pinning tests): the run walks
the agent graph node-by-node via ``agent.iter`` WITHOUT consuming
``node.stream`` — the terminal ``final`` carries ``run.result.output``.
Relaying streamed text deltas as ``chunk`` events is a later phase's scope
(P7 consumer surface): the pinned closed shape for P2 is
``started`` → ``final``/``failed``.

Capabilities (:func:`assemble_capabilities`) are driven entirely by the
``cfg.harness.*`` toggles (plan §3.4): memory → ``Memory`` +
``FileMemoryStore`` (D4: shared with ``MemoryEngine``), skills → ``Skills``,
ask_user → ``AskUser`` (D3 deferred wiring), subagents → ``SubAgents``
(in-process only, D1), and delegation → ``DelegationCapability`` (the
``delegate_run`` custom capability over the real CLI adapters, D1). Every toggle OFF ⇒ ``[]``
and the executor still runs (graceful degradation, asserted by the P2 tests).

The executor NEVER touches :func:`bob_harness.adapters.base.start_process` or
:func:`bob_harness.adapters.base.build_child_env` — asserted by the P2 tests
via monkeypatched AssertionError trips.

No side effects on import (no ``__main__`` block; only definitions).
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic_ai import Agent as V2Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.workspaces import LocalWorkspaceBackend

from bob_harness.adapters.base import RunSpec, StreamEvent
from bob_harness.config import (
    HarnessConfig,
    effective_router_settings,
)

__all__ = [
    "SESSION_REF_PREFIX",
    "ExecutorAdapter",
    "_build_agent",
    "assemble_capabilities",
]

# In-process run identity prefix (no OS session exists for this adapter).
SESSION_REF_PREFIX = "exec-"


def _model_from_config(cfg: HarnessConfig) -> OpenAIChatModel | str:
    """Build the config-default model: an ``OpenAIChatModel`` on the router,
    falling back to the bare model name when the provider cannot be built."""
    settings = effective_router_settings(cfg)
    model_name = str(settings["model"])
    try:
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=settings["base_url"], api_key=settings["api_key"]
            ),
        )
    except Exception:  # noqa: BLE001 - defensive name fallback keeps runs alive
        return model_name


def assemble_capabilities(cfg: HarnessConfig, workdir: str | Path | None = None) -> list[Any]:
    """Build the executor agent's capability list from the ``cfg.harness.*`` toggles.

    Every toggle OFF ⇒ ``[]`` (graceful degradation: the executor runs
    capability-free). Toggle-ON wiring per plan §3.2/§3.4:

    - ``memory``: ``Memory(store=FileMemoryStore(dir, workspace=...),
      agent_name=..., inject_memory=..., max_tokens=...)``; ``dir=''`` →
      ``<storage.data_dir>/memory`` (shared with ``MemoryEngine``, D4);
      ``workspace_root=''`` → per-run workdir (the ``workdir`` arg, else
      ``storage.data_dir``). ``FileMemoryStore`` REQUIRES ``workspace=``
      (verified 0.73.0: ``UserError`` otherwise).
    - ``skills``: ``Skills(directories=[<storage.data_dir>/skills], ...)``
      with the include/exclude slug filters and ``read_files``; missing skill
      directories are tolerated per ``missing_directories`` (lazy ctor: no
      mkdir happens at assembly time, verified).
    - ``ask_user``: ``AskUser(id=..., answerer=None, timeout=None)`` — the
      D3 ``deferred`` wiring (the run parks via ``DeferredToolRequests``; the
      TaskLoop gate answers on resume, P6).
    - ``subagents``: ``SubAgents(agents=[], max_depth=...)`` — in-process
      only (D1); P3+ populates nested helper agents (never hermes/opencode).
    - ``delegation``: ``DelegationCapability`` (the ``delegate_run`` custom
      capability, D1) over the REAL hermes/opencode registry adapters —
      never a ``SubAgents`` entry for either CLI.
    """
    group = cfg.harness
    if not (
        group.memory.enabled
        or group.skills.enabled
        or group.ask_user.enabled
        or group.subagents.enabled
        or group.delegation.enabled
    ):
        # Every toggle off — incl. delegation since P3 (D1) — contributes no
        # capability object, so an all-off group yields [].
        return []

    capabilities: list[Any] = []
    from pydantic_ai_harness.ask_user import AskUser
    from pydantic_ai_harness.memory import FileMemoryStore, Memory
    from pydantic_ai_harness.skills import Skills
    from pydantic_ai_harness.sub_agents import SubAgents

    if group.memory.enabled:
        memory_dir = group.memory.dir or str(Path(cfg.storage.data_dir) / "memory")
        workspace_root = group.memory.workspace_root or workdir or cfg.storage.data_dir
        store = FileMemoryStore(memory_dir, workspace=LocalWorkspaceBackend(workspace_root))
        capabilities.append(
            Memory(
                store=store,
                agent_name=group.memory.agent_name,
                namespace="",
                inject_memory=group.memory.inject,
                max_tokens=group.memory.max_tokens,
            )
        )

    if group.skills.enabled:
        capabilities.append(
            Skills(
                directories=[Path(cfg.storage.data_dir) / "skills"],
                include=group.skills.include or None,
                exclude=group.skills.exclude or None,
                missing_directories=group.skills.missing_directories,
                read_files=group.skills.read_files,
            )
        )

    if group.ask_user.enabled:
        capabilities.append(AskUser(id=group.ask_user.id, answerer=None, timeout=None))

    if group.subagents.enabled:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            capabilities.append(SubAgents(agents=[], max_depth=group.subagents.max_depth))

    if group.delegation.enabled:
        from bob_harness.adapters import get as adapters_get
        from bob_harness.delegation import DelegationCapability

        capabilities.append(
            DelegationCapability(
                registry={
                    name: adapters_get(name, cfg)
                    for name in ("hermes", "opencode")
                },
                cfg=cfg,
            )
        )

    return capabilities


def _build_agent(cfg: HarnessConfig, workdir: str | Path | None = None) -> V2Agent:
    """Build a pydantic-ai 2.x Agent per the harness config (plan §3.2).

    Capabilities are assembled per the ``cfg.harness.*`` toggles; the default
    model is the config router model (``OpenAIChatModel`` on
    ``router.base_url``). Per-run overrides arrive via ``run(spec.model=...)``.
    """
    return V2Agent(
        _model_from_config(cfg),
        name="executor",
        capabilities=assemble_capabilities(cfg, workdir=workdir),
    )


def _error_text(exc: BaseException) -> str:
    """Terminal-error text: exception type + message (never re-raised)."""
    return f"{type(exc).__name__}: {exc}"


class ExecutorAdapter:
    """In-process pydantic-ai executor: zero subprocesses, capability-driven."""

    def __init__(self, config: HarnessConfig) -> None:
        """Store the FULL :class:`~bob_harness.config.HarnessConfig` (the adapter
        reads ``cfg.router`` for the default model and ``cfg.harness.*`` for
        capability toggles; the agent is built per run with the run's workdir
        bound as workspace root)."""
        self.cfg = config

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        """Yield the run stream: ``started`` then exactly one terminal event.

        The run executes on an in-process pydantic-ai 2.x Agent with the
        per-run workspace bound from ``spec.workdir``
        (``LocalWorkspaceBackend(spec.workdir)``; ``None`` keeps the default).
        ``spec.model`` overrides the config-default model for this run.

        Stream scope (OQ2, per the committed P2 pinning tests): the graph is
        walked node-by-node via ``agent.iter`` without consuming
        ``node.stream``; the terminal ``final`` carries the run's validated
        output (``run.result.output``). Streamed text-delta relaying as
        ``chunk`` events is a later phase's scope (P7 consumer surface).

        Model/run exceptions are converted to exactly one ``failed`` event
        and never re-raised. ``spec.oneway`` and other fire-and-forget hints
        ride along unchanged — no gate/ask machinery is engaged here.
        """
        session_ref = f"{SESSION_REF_PREFIX}{uuid.uuid4().hex[:12]}"
        yield StreamEvent(kind="started", session_ref=session_ref)

        workdir = spec.workdir
        agent = V2Agent(
            _model_from_config(self.cfg),
            name="executor",
            capabilities=assemble_capabilities(self.cfg, workdir=workdir),
        )
        workspace = LocalWorkspaceBackend(workdir) if workdir else None
        try:
            async with agent.iter(
                spec.prompt,
                model=spec.model,
                workspace=workspace,
            ) as run:
                async for _node in run:  # node walk drives the graph to completion
                    pass
                result = run.result
            final_text = result.output if result is not None else ""
            if not isinstance(final_text, str):
                final_text = str(final_text)
            yield StreamEvent(kind="final", final_text=final_text)
        except Exception as exc:  # noqa: BLE001 - terminal failed event, never re-raised
            yield StreamEvent(kind="failed", error=_error_text(exc))
