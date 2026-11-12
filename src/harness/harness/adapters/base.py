"""Agent adapter contract and shared process helpers (ARCHITECTURE.md section 2.2).

Phase R4 contract shipped by this module:

- :class:`RunSpec` — the per-run request handed to every adapter: the prompt,
  the hermes profile (``None`` for non-profile chat), an opencode workdir, an
  optional resume reference, an optional timeout override (``None`` → adapter
  default from config), a ``oneway`` fire-and-forget flag, attachment file
  paths, the opencode ``--continue`` semantic (``resume_requested``), and
  optional per-run model/agent overrides (``spec.model`` / ``spec.agent`` for
  the hermes ``-m`` / opencode ``--model`` and opencode ``--agent`` argv
  entries; they fall back to the adapter's config-driven defaults).
- :class:`StreamEvent` — one relayed event of a run stream. The ``kind`` field
  is a closed set (:data:`EVENT_KINDS`); constructing an event with an unknown
  kind raises ``ValueError`` (fail fast on adapter bugs instead of silently
  leaking un-consumed event types downstream in the R6 orchestrator).
- :class:`AgentRun` — runtime-checkable protocol: an object whose ``run(spec)``
  *call* returns an async iterator of :class:`StreamEvent` directly (an async
  generator function); callers never ``await run(...)`` itself.
- Process primitives shared by hermes and opencode adapters: children are
  spawned in their own POSIX session (:func:`start_process` with
  ``start_new_session=True``) so the whole child tree can be reaped with one
  ``killpg`` (:func:`kill_process_group`), used by the timeout watchdog and by
  early-consumer abandonment (GeneratorExit) cleanup.
- :func:`build_child_env` — the strict child-env allowlist builder. Children
  must never see harness-internal secrets: anything matching ``HARNESS_*`` is
  always denied (the base env is *filtered* down to the allowlist, and any
  ``HARNESS_*`` key in the extra/passthrough map is an assertion failure).

No side effects on import (no ``__main__`` block; only definitions).
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "EVENT_KINDS",
    "AgentRun",
    "RunSpec",
    "StreamEvent",
    "build_child_env",
    "kill_process_group",
    "read_stream",
    "start_process",
]

# Closed set of StreamEvent kinds (fail fast on unknown kinds downstream).
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "started",  # child spawned / run began
        "chunk",  # incremental text relayed from the child
        "final",  # semantic final text of the run (opencode/fake adapters)
        "tool",  # a tool invocation surfaced by the child
        "failed",  # terminal failure (error, timeout, exit != 0)
        "system",  # harness/system-level relay not owned by the child model
    }
)

# Harness-internal env prefix that must never leak into a child process.
ENV_DENY_PREFIX = "HARNESS_"

# Base-env keys a child CLI may inherit (everything else is dropped).
DEFAULT_ENV_KEEP: tuple[str, ...] = ("PATH", "HOME", "TERM")


@dataclass(frozen=True)
class StreamEvent:
    """One event relayed from an adapter run stream.

    Attributes are per-kind: ``text`` for ``chunk``, ``final_text`` for
    ``final``, ``tool_name``/``tool_brief`` for ``tool``, ``error`` (plus
    ``exit_code`` when known) for ``failed``, ``raw`` for unstructured
    ``system`` relays; ``session_ref`` is set when the adapter knows the
    child's session identity. Unknown kinds raise ``ValueError`` on
    construction (closed set, :data:`EVENT_KINDS`).
    """

    kind: str
    text: str | None = None
    final_text: str | None = None
    tool_name: str | None = None
    tool_brief: str | None = None
    error: str | None = None
    exit_code: int | None = None
    session_ref: str | None = None
    raw: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(
                f"unknown StreamEvent kind {self.kind!r}; valid kinds: {sorted(EVENT_KINDS)}"
            )


@dataclass
class RunSpec:
    """One run request handed to an adapter (ARCHITECTURE.md section 2.2).

    ``timeout_s=None`` means "adapter default from config"; ``resume_ref`` is
    the opencode ``--session`` id / hermes ``--resume`` id; ``resume_requested``
    (opencode) requests the child continue its latest session when no
    ``resume_ref`` is set (``--continue``); ``model`` / ``agent`` are optional
    per-run overrides of the config default model / agent; ``oneway`` and
    ``files`` are reserved call-shape / attachment hints the phase R4 adapters
    do not act on (documented for the R6 orchestrator contract).
    """

    prompt: str
    profile: str | None = None
    workdir: str | None = None
    resume_ref: str | None = None
    timeout_s: int | None = None
    oneway: bool = False
    files: list[str] = field(default_factory=list)
    resume_requested: bool = False
    model: str | None = None
    agent: str | None = None


@runtime_checkable
class AgentRun(Protocol):
    """Runtime-checkable adapter protocol (ARCHITECTURE.md section 2.2).

    ``run(spec)`` is called WITHOUT awaiting and must return an
    :class:`~collections.abc.AsyncIterator` of :class:`StreamEvent` directly
    (i.e. the adapter implements it as an async generator function).
    """

    def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]: ...


async def start_process(
    argv: list[str],
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> tuple[asyncio.subprocess.Process, bool]:
    """Spawn ``argv`` (list form, no shell, stdin=DEVNULL) in a new session.

    Returns ``(proc, created_pg)`` where ``created_pg`` is True because the
    child is its own process-group leader (``start_new_session=True``), so
    :func:`kill_process_group` can reap the whole child tree with ``killpg``.
    stdout/stderr are PIPE (adapters consume them themselves).
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return proc, True


async def kill_process_group(proc: asyncio.subprocess.Process, grace_s: float = 5) -> None:
    """Reap ``proc``'s whole process group: SIGTERM, wait ``grace_s``, SIGKILL.

    SIGTERM is delivered to the process group (the child is a session leader
    via :func:`start_process`, so ``proc.pid`` is the pgid); if the group has
    not exited within ``grace_s`` seconds, SIGKILL is delivered to the group
    and we wait unconditionally. Already-dead processes and vanished process
    groups are no-ops. This helper is shared by the timeout watchdog paths of
    the hermes and opencode adapters (plan F2).
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
        return
    except TimeoutError:  # asyncio.TimeoutError is the builtin alias on 3.11+
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except ProcessLookupError:  # pragma: no cover - raced with the reaper
        return


def build_child_env(
    base_env: Mapping[str, str],
    extra: Mapping[str, str] | None = None,
    *,
    keep: tuple[str, ...] = DEFAULT_ENV_KEEP,
    keep_prefixes: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build a child process env from ``base_env`` with a strict allowlist.

    Included: ``keep`` keys (default PATH/HOME/TERM) present in ``base_env``,
    plus every ``base_env`` key matching one of ``keep_prefixes`` (opencode
    passes ``OPENCODE_*``), plus the caller's ``extra`` mapping verbatim (hermes
    adds ``HERMES_HOME`` here, and profile ``.env`` passthrough keys). The
    hermes adapter calls this with ``keep=("PATH", "HOME", "TERM",
    "HERMES_HOME")``.

    Nothing matching the ``HARNESS_*`` deny prefix can ever be in the result:
    ``HARNESS_*`` keys are dropped from ``base_env`` (harness settings and
    secrets such as ``HARNESS_API_KEY``, ``HARNESS_ROUTER_*`` never reach a
    child CLI) and a ``HARNESS_*`` key inside ``extra`` trips an AssertionError
    (belt-and-braces against bad passthrough maps). The final mapping is
    asserted HARNESS_-free before returning (plan M-3).
    """
    result: dict[str, str] = {}
    for key, value in base_env.items():
        if key.startswith(ENV_DENY_PREFIX):
            continue
        if key in keep or any(key.startswith(prefix) for prefix in keep_prefixes):
            result[key] = value
    for key, value in (extra or {}).items():
        if key.startswith(ENV_DENY_PREFIX):
            raise AssertionError(f"build_child_env: refusing HARNESS_* passthrough key {key!r}")
        result[key] = value
    leaked = [key for key in result if key.startswith(ENV_DENY_PREFIX)]
    assert not leaked, f"build_child_env: HARNESS_* leaked into child env: {leaked}"
    return result


async def read_stream(stream: asyncio.StreamReader | None) -> bytes:
    """Drain a child pipe to EOF (best-effort), returning the captured bytes.

    Adapters launch one :func:`read_stream` task per spawned child (stderr
    accumulation) and only await it after the child is confirmed dead, so the
    pipe always reaches EOF in practice; on the edge (abandoned run, pipe
    error) whatever was buffered is returned and cancellation is absorbed.
    """
    if stream is None:  # pragma: no cover - adapters always PIPE
        return b""
    try:
        return await stream.read()
    except (asyncio.CancelledError, OSError):
        return b""
