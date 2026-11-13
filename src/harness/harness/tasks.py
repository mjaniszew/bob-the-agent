"""Task-folder schema and lifecycle FSM (ARCHITECTURE.md section 2.9, rev2; D14/D15/D17).

Implements the schema layer of the task-folder loop: ARCHITECTURE.md sections
2.9 / 2.9.2 / 2.9.3 (task folder, task.yaml schema, lifecycle FSM), the
taskloop.replay() primitives section 3.4 needs (F17), the flow of section 3.5,
failure modes F13/F14 of section 8 and test contract TEST_PLAN.md row 19 of
the approved R9 breakdown. DECISIONS.md D14 (the inbox directory IS the queue),
D15 (folder + task.yaml + harness-owned state) and D17 (SUBTASK_DROP chaining,
max_depth-1 arithmetic) govern this module. The loop itself — watcher, single
runner, pickup orchestration — is taskloop.py (WP2): this module only provides
the store/schema/FSM primitives (section 3.5 steps 1-2, 6).

One task = folder ``<storage.data_dir>/tasks/<id>/`` (id = 8-char base32, the
session-id alphabet, ARCHITECTURE.md section 2.9.2) holding:

- ``task.yaml`` — REQUIRED (D15; loaded with safe_load, unknown keys rejected):
  ``description`` (required, non-empty, <= ``tasks.max_desc_chars`` (4000) chars)
  plus optional ``agent`` hint, ``workdir`` (must sit under ``^/app/projects/``
  and exist at validation time), ``depends_on`` (list of task ids), ``max_depth``
  (per-task depth-cap override), ``oneway`` (explicit gating opt-out, D18) and
  ``parent``/``origin`` (written by the drop/pickup machinery; validated when
  present). Unknown keys are rejected by name (the ``extra="forbid"`` philosophy
  of config.py) and ``id`` specifically: the folder name IS the id. No ``due``,
  no ``priority``, no ``assignee`` — PoC minimalism, documented extension point.
- ``state.json`` — harness-owned rewrite-ok lifecycle header
  ``{id, state, attempt, agent_session, parent, origin, depth, updated}``
  (module constant ``STATE_KEYS``), atomically rewritten through
  :class:`harness.persist.AtomicCounter` on every state move; ``updated`` is an
  ISO-8601 UTC ``Z`` timestamp at seconds precision (the harness.session._now
  format).
- ``events.jsonl`` — append-only audit through :class:`harness.persist.JsonlWriter`;
  every line is ``{"ts", "task_id", "kind", "data"}`` with ``kind`` from the
  closed ``EVENTS`` registry (unknown kinds raise ``ValueError``, mirroring
  session.py). Read back with :func:`harness.persist.read_jsonl`.
- ``result.md`` — written once on DONE by :meth:`TaskDir.write_result`
  (final text + a small meta header).

Lifecycle (ARCHITECTURE.md section 2.9.3; module constant ``TRANSITIONS``):
``appeared -> ANALYZING -> ANALYZED -> READY -> RUNNING -> DONE`` with parking
(``READY/WAITING_DEPS <->``), gating (``RUNNING <-> WAITING_INPUT``, gate timeout
``-> FAILED``) and honest failure (``ANALYZING -> FAILED(syntax)`` for invalid
task.yaml, ``-> FAILED(cycle)`` for F14 cycles). Every move is guarded BEFORE
any write: an illegal move raises :class:`TaskFSMError` and leaves state.json
and events.jsonl untouched (SessionFSMError semantics). Terminal states (DONE,
FAILED) reject every move; ``INTERRUPTED`` is a legal state written only by
boot replay (F17; section 3.4) and re-queues to READY.

Failure modes baked in here (ARCHITECTURE.md section 8): F13 (duplicate id /
double pickup fails loudly — :meth:`TaskStore.move_from_inbox` refuses an
existing destination), F14 (unknown or cyclic ``depends_on`` rejected at
validation and ready-check time via :func:`validate_depends` /
:func:`detect_cycle`) and F17 (``INTERRUPTED`` exists as a state, replay is
idempotent). SUBTASK_DROP chaining (D17): ``harness.blocks`` parses the fenced
blocks; :func:`validate_subtask_drop` validates the parsed field mapping and
:func:`subtask_depth_cap` computes the ``max_depth - 1`` child cap with the
"global default 2, floor 1" rule — the cap is always a parameter, never
hardcoded, and a cap <= 0 means the drop is refused.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from harness.config import HarnessConfig
from harness.errors import ConfigError
from harness.persist import AtomicCounter, JsonlWriter
from harness.session import new_session_id

__all__ = [
    "DEFAULT_MAX_DESC_CHARS",
    "DEFAULT_TASKS_MAX_DEPTH",
    "EVENTS",
    "STATES",
    "STATE_KEYS",
    "TASK_YAML_KEYS",
    "TRANSITIONS",
    "TaskDir",
    "TaskExistsError",
    "TaskFSMError",
    "TaskSchemaError",
    "TaskStore",
    "detect_cycle",
    "load_task_yaml",
    "subtask_depth_cap",
    "validate_depends",
    "validate_subtask_drop",
    "validate_task_yaml",
]

_LOG = logging.getLogger("harness.tasks")

_TASK_YAML_FILENAME = "task.yaml"
_STATE_FILENAME = "state.json"
_EVENTS_FILENAME = "events.jsonl"
_RESULT_FILENAME = "result.md"

# Task ids: 8-char base32 (A-Z2-7) — the session-id alphabet (ARCH 2.9.2).
_ID_PATTERN = re.compile(r"[A-Z2-7]{8}")

# A task workdir must sit under the project root (design: ^/app/projects/).
_WORKDIR_PREFIX = "/app/projects/"

# Char budget for task.yaml `description` — mirrors tasks.max_desc_chars default.
DEFAULT_MAX_DESC_CHARS = 4000

# Global subtask depth cap default (D17: "global default 2, floor 1"); the
# effective cap is always a parameter — cfg.tasks.max_depth is read by callers.
DEFAULT_TASKS_MAX_DEPTH = 2

# task.yaml keys (D15; order = normalized output order and dump order). No id,
# no due/priority/assignee: the folder name is the id, PoC minimalism.
TASK_YAML_KEYS: tuple[str, ...] = (
    "description",
    "agent",
    "workdir",
    "depends_on",
    "max_depth",
    "oneway",
    "parent",
    "origin",
)

# SUBTASK_DROP fenced-block field mappings (D17): description + optional agent
# hint + optional depends_on. blocks.py parses; this module validates.
_SUBTASK_DROP_KEYS: tuple[str, ...] = ("description", "agent", "depends_on")

_AGENT_HINTS: tuple[str, ...] = ("hermes", "opencode")
_ORIGINS: tuple[str, ...] = ("user", "agent", "scheduler", "api")

# The 8 state.json keys, in their contract order (rewrite-ok header, R9 order).
STATE_KEYS: tuple[str, ...] = (
    "id",
    "state",
    "attempt",
    "agent_session",
    "parent",
    "origin",
    "depth",
    "updated",
)

# Task lifecycle FSM (ARCHITECTURE.md section 2.9.3). INTERRUPTED is written
# by boot replay only (F17, section 3.4) — never a runtime move. DONE/FAILED
# are terminal for the PoC (empty target sets reject every move).
TRANSITIONS: dict[str, set[str]] = {
    "appeared": {"ANALYZING"},
    "ANALYZING": {"ANALYZED", "FAILED", "INTERRUPTED"},
    "ANALYZED": {"READY", "WAITING_DEPS", "INTERRUPTED"},
    "READY": {"RUNNING", "WAITING_DEPS", "INTERRUPTED"},
    "RUNNING": {"WAITING_INPUT", "DONE", "FAILED", "INTERRUPTED"},
    "WAITING_DEPS": {"READY", "INTERRUPTED"},
    "WAITING_INPUT": {"RUNNING", "FAILED", "INTERRUPTED"},
    "DONE": set(),
    "FAILED": set(),
    "INTERRUPTED": {"READY"},
}
STATES: set[str] = set(TRANSITIONS) | {
    target for targets in TRANSITIONS.values() for target in targets
}

# events.jsonl kinds (closed registry, like session.py EVENTS; D15/D18/3.5).
EVENTS: set[str] = {
    "appeared",
    "state_change",
    "gate_opened",
    "gate_answered",
    "note",
    "subtask_drop",
    "subtask_refused",
    "dep_wait",
    "result_written",
}


# ---------------------------------------------------------------------------
# Errors (harness.errors pattern: ConfigError(key, message) catch-family;
# errors.py itself is not modified by WP1)
# ---------------------------------------------------------------------------
class TaskSchemaError(ConfigError):
    """A task.yaml file or SUBTASK_DROP mapping failed schema validation (D15).

    ``key`` names the subject (``"task.yaml"``, ``"SUBTASK_DROP"`` or the
    offending field); ``message`` names the problem (and the offending key or
    value) readably. Raised before any file is written.
    """


class TaskFSMError(ConfigError):
    """Illegal task state-machine transition (SessionFSMError semantics).

    Raised by :meth:`TaskDir.transition` when the move is not in
    :data:`TRANSITIONS` — before any write, so state.json and events.jsonl are
    left untouched. ``old`` is ``None`` when the task has no state.json yet.

    Attributes:
        task_id: id of the task whose move was rejected (the inherited
            ``ConfigError`` ``key`` attribute).
        old: state the task was in before the rejected move (or ``None``).
        new: state the rejected move tried to reach.
    """

    def __init__(self, task_id: str, old: str | None, new: str) -> None:
        self.task_id = task_id
        self.old = old
        self.new = new
        if old is None:
            message = f"task {task_id}: no state.json; cannot transition to {new}"
        else:
            message = f"task {task_id}: illegal transition {old} -> {new}"
        super().__init__(task_id, message)


class TaskExistsError(ConfigError):
    """A task id already exists where uniqueness matters (F13 dedupe).

    Raised by :meth:`TaskStore.move_from_inbox` (destination already picked up)
    and :meth:`TaskStore.create_inbox_task` (duplicate forced id). Messages name
    both involved paths so the failure is loud on disk inspection.
    """


def _now() -> str:
    """UTC clock for state/events timestamps: the harness.session._now format
    (ISO-8601 with seconds precision and a ``Z`` suffix)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_task_id(value: object) -> bool:
    """True iff ``value`` is a valid task id: 8 chars from A-Z2-7."""
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _require_task_id(task_id: str) -> str:
    """Validate a caller-supplied task id (path-safety: dirs are named by id)."""
    if not _is_task_id(task_id):
        raise ValueError(f"invalid task id {task_id!r}: expected 8 chars from A-Z2-7")
    return task_id


# ---------------------------------------------------------------------------
# Schema validation (D15 — usable standalone, without a store)
# ---------------------------------------------------------------------------
def validate_task_yaml(
    raw: object,
    *,
    max_desc_chars: int = DEFAULT_MAX_DESC_CHARS,
    workdir_prefix: str = _WORKDIR_PREFIX,
) -> dict:
    """Validate one parsed task.yaml mapping and return its normalized form.

    Standalone D15 validator (taskloop can validate without a store): ``raw``
    is the ``yaml.safe_load`` result, ``max_desc_chars`` the description cap
    (``tasks.max_desc_chars``) and ``workdir_prefix`` the required workdir
    root (default ``/app/projects/``; tests may point it at a tmp dir). The
    workdir is checked for existence only when present.

    Returns a dict with every ``TASK_YAML_KEYS`` entry in contract order
    (``description`` kept verbatim; ``agent``/``workdir``/``max_depth``/
    ``parent``/``origin`` ``None`` when absent; ``depends_on`` ``[]``, deduped
    order-preserving; ``oneway`` ``False`` default). Raises
    :class:`TaskSchemaError` naming the problem: non-mapping document, unknown
    key (``id`` specially), missing/empty/oversized/non-str description, bad
    agent hint, bad origin enum, bad workdir prefix or missing directory,
    non-list or ill-formed-id ``depends_on``, non-int (or negative)
    ``max_depth``, non-bool ``oneway``, ill-formed ``parent``.
    """
    source = "task.yaml"
    if not isinstance(raw, dict):
        raise TaskSchemaError(source, f"{source} must be a YAML mapping, got {type(raw).__name__}")
    unknown = sorted(str(key) for key in raw if key not in TASK_YAML_KEYS)
    if unknown:
        if unknown == ["id"]:
            raise TaskSchemaError(
                source,
                f"{source}: unknown key 'id' — the folder name is the task id; "
                f"task.yaml must not carry an id field",
            )
        raise TaskSchemaError(
            source,
            f"{source}: unknown key(s) {', '.join(repr(key) for key in unknown)}; "
            f"allowed keys: {', '.join(TASK_YAML_KEYS)}",
        )

    if "description" not in raw:
        raise TaskSchemaError("description", f"{source}: missing required field 'description'")
    description = raw["description"]
    if not isinstance(description, str):
        raise TaskSchemaError(
            "description",
            f"{source}: field 'description' must be a string, got {type(description).__name__}",
        )
    if not description.strip():
        raise TaskSchemaError("description", f"{source}: field 'description' must be non-empty")
    if len(description) > max_desc_chars:
        raise TaskSchemaError(
            "description",
            f"{source}: field 'description' is {len(description)} chars (max {max_desc_chars})",
        )

    agent = raw.get("agent")
    if agent is not None and agent not in _AGENT_HINTS:
        raise TaskSchemaError(
            "agent", f"{source}: field 'agent' must be one of {sorted(_AGENT_HINTS)}, got {agent!r}"
        )

    workdir = raw.get("workdir")
    if workdir is not None:
        if not isinstance(workdir, str):
            raise TaskSchemaError(
                "workdir",
                f"{source}: field 'workdir' must be a string, got {type(workdir).__name__}",
            )
        if not workdir.startswith(workdir_prefix):
            raise TaskSchemaError(
                "workdir",
                f"{source}: field 'workdir' {workdir!r} must start with {workdir_prefix!r}",
            )
        if not Path(workdir).is_dir():
            raise TaskSchemaError(
                "workdir", f"{source}: field 'workdir' {workdir!r} does not exist (or is not a dir)"
            )

    raw_depends = raw.get("depends_on")
    if raw_depends is None:
        raw_depends = []
    elif isinstance(raw_depends, list):
        for element in raw_depends:
            if not _is_task_id(element):
                raise TaskSchemaError(
                    "depends_on",
                    f"{source}: field 'depends_on' must be a list of 8-char A-Z2-7 task ids; "
                    f"bad entry {element!r}",
                )
        raw_depends = list(dict.fromkeys(raw_depends))
    else:
        raise TaskSchemaError(
            "depends_on",
            f"{source}: field 'depends_on' must be a list of task ids, "
            f"got {type(raw_depends).__name__}",
        )

    max_depth = raw.get("max_depth")
    if max_depth is not None and (
        isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0
    ):
        raise TaskSchemaError(
            "max_depth", f"{source}: field 'max_depth' must be an int >= 0, got {max_depth!r}"
        )

    oneway = raw.get("oneway")
    if oneway is None:
        oneway = False
    if not isinstance(oneway, bool):
        raise TaskSchemaError(
            "oneway", f"{source}: field 'oneway' must be a boolean, got {oneway!r}"
        )

    parent = raw.get("parent")
    if parent is not None and not _is_task_id(parent):
        raise TaskSchemaError(
            "parent",
            f"{source}: field 'parent' must be a task id (8 chars, A-Z2-7), got {parent!r}",
        )

    origin = raw.get("origin")
    if origin is not None and origin not in _ORIGINS:
        raise TaskSchemaError(
            "origin", f"{source}: field 'origin' must be one of {sorted(_ORIGINS)}, got {origin!r}"
        )

    return {
        "description": description,
        "agent": agent,
        "workdir": workdir,
        "depends_on": raw_depends,
        "max_depth": max_depth,
        "oneway": oneway,
        "parent": parent,
        "origin": origin,
    }


def load_task_yaml(
    path: str | os.PathLike,
    *,
    max_desc_chars: int = DEFAULT_MAX_DESC_CHARS,
    workdir_prefix: str = _WORKDIR_PREFIX,
) -> dict:
    """Read, ``yaml.safe_load`` and validate one task.yaml file (D15).

    Raises :class:`TaskSchemaError` naming the file for: a missing/unreadable
    file, invalid YAML (readable, with the parser's line/column detail) and
    every :func:`validate_task_yaml` schema problem.
    """
    task_yaml_path = Path(path)
    try:
        text = task_yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskSchemaError(str(task_yaml_path), f"task.yaml not found: {task_yaml_path}") from exc
    except OSError as exc:
        raise TaskSchemaError(
            str(task_yaml_path), f"task.yaml unreadable ({task_yaml_path}): {exc}"
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TaskSchemaError(
            str(task_yaml_path), f"invalid YAML in {task_yaml_path}: {exc}"
        ) from exc
    return validate_task_yaml(raw, max_desc_chars=max_desc_chars, workdir_prefix=workdir_prefix)


def validate_depends(depends_on: list[str], known: Mapping[str, str]) -> None:
    """F14 guard for a dependency list: format + existence.

    Every id must be an 8-char A-Z2-7 task id AND exist in ``known`` (a mapping
    id -> state over all tasks known to the caller). Raises
    :class:`TaskSchemaError` naming the offending id.
    """
    for dep in depends_on:
        if not _is_task_id(dep):
            raise TaskSchemaError(
                "depends_on", f"invalid depends_on task id {dep!r}: expected 8 chars from A-Z2-7"
            )
        if dep not in known:
            raise TaskSchemaError(
                "depends_on", f"unknown depends_on id {dep!r}: no task with that id exists (F14)"
            )


def detect_cycle(ids: Iterable[str] | None, edges: Mapping[str, Iterable[str]]) -> None:
    """F14 guard: reject a cycle in the dependency/parent graph (iterative DFS).

    ``edges`` maps task id -> ids it depends on (or parent links as single-id
    lists); ``ids`` restricts the walk to nodes reachable from those roots
    (``None`` — or an empty set — walks the whole map). Raises
    :class:`TaskSchemaError` with the cycle path, e.g.
    ``dependency cycle detected: a -> b -> a`` (self-dependency included).
    """
    graph: dict[str, list[str]] = {
        node: [str(target) for target in targets] for node, targets in edges.items()
    }
    starts = sorted(set(ids)) if ids is not None else sorted(graph)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    for root in starts:
        if color.get(root, WHITE) != WHITE:
            continue
        color[root] = GRAY
        path = [root]
        iters = [iter(sorted(graph.get(root, ())))]
        while iters:
            advanced = False
            for neighbor in iters[-1]:
                state = color.get(neighbor, WHITE)
                if state == GRAY:
                    cycle = path[path.index(neighbor) :] + [neighbor]
                    raise TaskSchemaError(
                        "depends_on", "dependency cycle detected: " + " -> ".join(cycle)
                    )
                if state == WHITE:
                    color[neighbor] = GRAY
                    path.append(neighbor)
                    iters.append(iter(sorted(graph.get(neighbor, ()))))
                    advanced = True
                    break
            if not advanced:
                color[path.pop()] = BLACK
                iters.pop()


def subtask_depth_cap(
    parent_max_depth: int | None, *, default_max_depth: int = DEFAULT_TASKS_MAX_DEPTH
) -> int:
    """Effective SUBTASK_DROP depth cap for a parent task (D17).

    The parent's own ``max_depth`` override when set, otherwise the global
    default floored at 1 ("global default 2, floor 1"). The cap is a PARAMETER
    here — never hardcoded. Callers treat a returned cap <= 0 as refusal (D17:
    "at depth 0 the drop is refused") and write the child's task.yaml with
    ``max_depth = cap - 1`` ("applies max_depth-1").
    """
    if parent_max_depth is not None:
        return int(parent_max_depth)
    return max(int(default_max_depth), 1)


def validate_subtask_drop(
    fields: object, *, max_desc_chars: int = DEFAULT_MAX_DESC_CHARS
) -> dict:
    """Validate a parsed SUBTASK_DROP fenced-block mapping (D17).

    ``harness.blocks`` parses the block; this validates the field mapping:
    exactly ``description`` (required, non-empty str <= ``max_desc_chars``)
    plus optional ``agent`` hint and optional ``depends_on`` (well-formed task
    ids; existence is the writer's F14 check — :func:`validate_depends`).
    Unknown keys are rejected by name; a non-mapping body raises. Returns a
    normalized mapping ready for ``TaskStore.create_inbox_task`` kwargs.
    """
    tag = "SUBTASK_DROP"
    if not isinstance(fields, dict):
        raise TaskSchemaError(
            tag, f"{tag} body must be a YAML mapping, got {type(fields).__name__}"
        )
    unknown = sorted(str(key) for key in fields if key not in _SUBTASK_DROP_KEYS)
    if unknown:
        raise TaskSchemaError(
            tag,
            f"{tag}: unknown key(s) {', '.join(repr(key) for key in unknown)}; "
            f"allowed keys: {', '.join(_SUBTASK_DROP_KEYS)}",
        )
    if "description" not in fields:
        raise TaskSchemaError("description", f"{tag}: missing required field 'description'")
    description = fields["description"]
    if not isinstance(description, str):
        raise TaskSchemaError(
            "description", f"{tag}: field 'description' must be a string, got {type(description).__name__}"
        )
    if not description.strip():
        raise TaskSchemaError("description", f"{tag}: field 'description' must be non-empty")
    if len(description) > max_desc_chars:
        raise TaskSchemaError(
            "description",
            f"{tag}: field 'description' is {len(description)} chars (max {max_desc_chars})",
        )
    agent = fields.get("agent")
    if agent is not None and agent not in _AGENT_HINTS:
        raise TaskSchemaError(
            "agent", f"{tag}: field 'agent' must be one of {sorted(_AGENT_HINTS)}, got {agent!r}"
        )
    raw_depends = fields.get("depends_on")
    if raw_depends is None:
        raw_depends = []
    elif isinstance(raw_depends, list):
        for element in raw_depends:
            if not _is_task_id(element):
                raise TaskSchemaError(
                    "depends_on",
                    f"{tag}: field 'depends_on' must be a list of 8-char A-Z2-7 task ids; "
                    f"bad entry {element!r}",
                )
        raw_depends = list(dict.fromkeys(raw_depends))
    else:
        raise TaskSchemaError(
            "depends_on",
            f"{tag}: field 'depends_on' must be a list of task ids, got {type(raw_depends).__name__}",
        )
    return {"description": description, "agent": agent, "depends_on": raw_depends}


# ---------------------------------------------------------------------------
# TaskDir: the FSM + file handle for one picked-up task
# ---------------------------------------------------------------------------
def _write_durable(path: Path, text: str) -> int:
    """Atomically write ``text`` at ``path`` (tmp + fsync + os.replace), the
    rewrite-ok file pattern of harness.persist.AtomicCounter, for plain-text
    files (result.md)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    return len(data)


class TaskDir:
    """The FSM + on-disk handle for ONE picked-up task folder.

    Owns the three files of ``tasks/<id>/`` through the harness.persist
    primitives: :class:`~harness.persist.AtomicCounter` for the rewrite-ok
    ``state.json`` and :class:`~harness.persist.JsonlWriter` for the
    append-only ``events.jsonl``. Async mutators are serialized behind a
    per-instance :class:`asyncio.Lock` (the session.py pattern); writes go
    through the lock-protected persist primitives. Construct via
    :meth:`birth` (a fresh pickup: atomically write the born ``appeared``
    state.json ONLY when none exists — F17 idempotency) or via
    :meth:`harness.tasks.TaskStore.open_task`.

    Attributes:
        id: 8-char base32 task id (also the directory name).
        state: current FSM state read from state.json (``None`` when the task
            has no state.json yet — inbox-born tasks before pickup).
    """

    def __init__(self, directory: Path, cfg: HarnessConfig, *, bus: Any | None = None) -> None:
        self._directory = Path(directory)
        self._id = self._directory.name
        self._cfg = cfg
        self._bus = bus
        self._counter = AtomicCounter(self._directory / _STATE_FILENAME)
        self._events = JsonlWriter(self._directory / _EVENTS_FILENAME)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def birth(
        cls,
        directory: Path | str,
        cfg: HarnessConfig,
        *,
        origin: str = "user",
        parent: str | None = None,
        depth: int = 0,
        agent_session: str | None = None,
        attempt: int = 0,
        bus: Any | None = None,
    ) -> TaskDir:
        """Initialize the born state.json of a just-picked-up task (idempotent).

        Atomically writes the full 8-key mapping
        ``{id, state="appeared", attempt, agent_session, parent, origin,
        depth, updated}`` — ONLY when state.json does not exist yet (F17 boot
        replay must never clobber an existing state, e.g. INTERRUPTED). The
        task dir itself must already exist (the pickup move made it). An
        invalid ``origin`` or ill-formed ``parent`` raises
        :class:`TaskSchemaError`.
        """
        task_directory = Path(directory)
        if not task_directory.is_dir():
            raise TaskSchemaError(
                str(task_directory), f"task dir {task_directory} does not exist (not picked up?)"
            )
        if origin not in _ORIGINS:
            raise TaskSchemaError(
                "origin", f"field 'origin' must be one of {sorted(_ORIGINS)}, got {origin!r}"
            )
        if parent is not None and not _is_task_id(parent):
            raise TaskSchemaError(
                "parent", f"field 'parent' must be a task id (8 chars, A-Z2-7), got {parent!r}"
            )
        state_path = task_directory / _STATE_FILENAME
        if not state_path.is_file():
            AtomicCounter(state_path).set(
                {
                    "id": task_directory.name,
                    "state": "appeared",
                    "attempt": int(attempt),
                    "agent_session": agent_session,
                    "parent": parent,
                    "origin": origin,
                    "depth": int(depth),
                    "updated": _now(),
                }
            )
            _LOG.info("task %s: born (state=appeared origin=%s parent=%s)", task_directory.name,
                      origin, parent)
        return cls(task_directory, cfg, bus=bus)

    # ------------------------------------------------------------------
    # Read-only view
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self._id

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def state(self) -> str | None:
        """Current FSM state read from state.json (``None`` when absent)."""
        state = self._counter.load().get("state")
        return state if isinstance(state, str) else None

    # ------------------------------------------------------------------
    # FSM + event writes (async: serialized behind the per-task lock)
    # ------------------------------------------------------------------
    async def transition(self, new_state: str, *, reason: str | None = None) -> None:
        """Validate ``new_state`` against :data:`TRANSITIONS` and move the FSM.

        The guard runs BEFORE any write: an illegal move (including moves out
        of the terminal states DONE/FAILED and unknown state names) raises
        :class:`TaskFSMError` leaving state.json and events.jsonl untouched.
        A legal move — under the per-task lock — atomically rewrites state.json
        (``state`` + ``updated``), then appends one ``state_change`` event
        ``{"old", "new", "reason"?}`` and, when a bus is attached, publishes
        ``task.state {task_id, old, new, reason?}`` (duck-typed, like
        session_manager.move).
        """
        old = self.state
        if new_state not in TRANSITIONS.get(old, set()):
            _LOG.warning("task %s: illegal transition %s -> %s", self._id, old, new_state)
            raise TaskFSMError(self._id, old, new_state)
        async with self._lock:
            self._counter.set({"state": new_state, "updated": _now()})
            data: dict = {"old": old, "new": new_state}
            if reason is not None:
                data["reason"] = reason
            self._events.append(
                {"ts": _now(), "task_id": self._id, "kind": "state_change", "data": data}
            )
            if self._bus is not None:
                payload: dict = {"task_id": self._id, "old": old, "new": new_state}
                if reason is not None:
                    payload["reason"] = reason
                await self._bus.publish("task.state", payload)

    async def append_event(self, kind: str, data: dict | None = None) -> None:
        """Append one audit line ``{"ts", "task_id", "kind", "data"}`` under the
        per-task lock. Unknown kinds raise :class:`ValueError` before any
        write (the closed ``EVENTS`` registry, mirroring session.py)."""
        if kind not in EVENTS:
            raise ValueError(f"unknown task event kind {kind!r}; expected one of {sorted(EVENTS)}")
        async with self._lock:
            self._events.append(
                {
                    "ts": _now(),
                    "task_id": self._id,
                    "kind": kind,
                    "data": dict(data) if data else {},
                }
            )

    # ------------------------------------------------------------------
    # Sync mutators (single-shot writes; file-level durability via persist)
    # ------------------------------------------------------------------
    def bump_attempt(self) -> int:
        """Atomically increment the ``attempt`` counter in state.json (M-8
        retries) and return the new attempt number."""
        return int(self._counter.bump("attempt")["attempt"])

    def write_result(
        self, md_text: str, *, agent: str | None = None, duration_s: float | None = None
    ) -> None:
        """Write ``result.md`` (the DONE artifact, section 2.9.2): a small meta
        header (task id, ts, agent, duration — the latter two only when
        recorded) followed by the final text, durably and atomically; then
        append the ``result_written`` event."""
        result_path = self._directory / _RESULT_FILENAME
        header_lines = [f"# Task result: {self._id}", "", f"- ts: {_now()}"]
        if agent is not None:
            header_lines.append(f"- agent: {agent}")
        if duration_s is not None:
            header_lines.append(f"- duration_s: {duration_s}")
        body = md_text if md_text.endswith("\n") else md_text + "\n"
        _write_durable(result_path, "\n".join(header_lines) + "\n\n---\n\n" + body)
        self._events.append(
            {
                "ts": _now(),
                "task_id": self._id,
                "kind": "result_written",
                "data": {"path": _RESULT_FILENAME},
            }
        )
        _LOG.info("task %s: result.md written", self._id)


# ---------------------------------------------------------------------------
# TaskStore: the tasks/ + tasks/inbox/ root (created lazily by writers)
# ---------------------------------------------------------------------------
class TaskStore:
    """The task-folder root: ``<data_dir>/tasks`` (picked-up) and
    ``<data_dir>/tasks/inbox`` (the queue, D14).

    Created with the harness config and an optional duck-typed bus (anything
    with ``async def publish(topic, payload)`` — the transition bus publishes
    are best-effort). Read paths never create directories; writers create the
    folder chain lazily.
    """

    def __init__(self, cfg: HarnessConfig, bus: Any | None = None) -> None:
        self._cfg = cfg
        self._bus = bus
        self._root = Path(cfg.storage.data_dir) / "tasks"
        self._inbox = Path(cfg.storage.data_dir) / cfg.tasks.inbox

    @property
    def root(self) -> Path:
        """Picked-up task dir root: ``<data_dir>/tasks``."""
        return self._root

    @property
    def inbox(self) -> Path:
        """The inbox queue: ``<data_dir>/tasks/inbox`` (D14; cfg.tasks.inbox)."""
        return self._inbox

    def bus(self) -> Any | None:
        """The attached duck-typed bus (None = no publishes)."""
        return self._bus

    # ------------------------------------------------------------------
    # Inbox drop (the ONLY inbox writer)
    # ------------------------------------------------------------------
    def create_inbox_task(
        self,
        description: str,
        *,
        origin: str,
        parent: str | None = None,
        depends_on: list[str] | None = None,
        agent: str | None = None,
        workdir: str | None = None,
        max_depth: int | None = None,
        oneway: bool = False,
        task_id: str | None = None,
    ) -> str:
        """Write a fresh inbox task ``inbox/<id>/task.yaml`` and return the id.

        The id is a fresh 8-char base32 (session-id alphabet, ARCH 2.9.2) or
        the ``task_id`` param (forced for tests; an ill-formed one raises
        ``ValueError``). The YAML is produced by validating the given fields
        through :func:`validate_task_yaml` (schema errors BEFORE any write);
        default-valued optional fields are omitted from the dump. A duplicate
        forced id raises :class:`TaskExistsError` (F13). NO state.json yet —
        state is born at pickup (the move + TaskDir.birth in taskloop).
        """
        if not isinstance(origin, str):
            raise TaskSchemaError("origin", f"field 'origin' must be a string, got {type(origin).__name__}")
        raw: dict = {"description": description, "origin": origin}
        if agent is not None:
            raw["agent"] = agent
        if workdir is not None:
            raw["workdir"] = workdir
        if depends_on is not None:
            raw["depends_on"] = list(depends_on)
        if max_depth is not None:
            raw["max_depth"] = max_depth
        if oneway:
            raw["oneway"] = True
        if parent is not None:
            raw["parent"] = parent
        normalized = validate_task_yaml(raw, max_desc_chars=int(self._cfg.tasks.max_desc_chars))
        task_id = task_id if task_id is not None else new_session_id()
        _require_task_id(task_id)
        folder = self._inbox / task_id
        try:
            folder.mkdir(parents=True)
        except FileExistsError as exc:
            raise TaskExistsError(
                str(task_id),
                f"inbox task {task_id} already exists: refusing to overwrite {folder} (F13 dedupe)",
            ) from exc
        payload = {
            key: value
            for key, value in normalized.items()
            if value is not None
            and not (key == "depends_on" and value == [])
            and not (key == "oneway" and value is False)
        }
        task_yaml_path = folder / _TASK_YAML_FILENAME
        with open(task_yaml_path, "xb") as fh:
            fh.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        _LOG.info(
            "inbox task %s created (origin=%s parent=%s depth_cap=%s)",
            task_id,
            origin,
            parent,
            max_depth,
        )
        return task_id

    # ------------------------------------------------------------------
    # Pickup move primitive (section 3.5; F13/F17 key)
    # ------------------------------------------------------------------
    def move_from_inbox(self, task_id: str) -> Path:
        """Atomically MOVE ``inbox/<id>/`` to ``tasks/<id>/`` and return the
        destination (the pickup primitive taskloop calls before
        :meth:`TaskDir.birth`).

        ``os.replace``-based (same volume), with a ``shutil.move`` fallback for
        cross-device volumes. An unknown inbox id raises :class:`TaskSchemaError`
        naming the missing path; an existing destination raises
        :class:`TaskExistsError` naming BOTH paths (F13: idempotent-by-failure,
        the second pickup never silently merges with the first).
        """
        _require_task_id(task_id)
        source = self._inbox / task_id
        destination = self._root / task_id
        if not source.is_dir():
            raise TaskSchemaError(
                str(task_id), f"no inbox task {task_id}: {source} does not exist"
            )
        if destination.exists():
            raise TaskExistsError(
                str(task_id),
                f"task {task_id} already picked up: refusing to move {source} -> "
                f"{destination} (destination exists, F13 dedupe)",
            )
        try:
            os.replace(source, destination)
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise TaskExistsError(
                    str(task_id),
                    f"task {task_id} already picked up: refusing to move {source} -> "
                    f"{destination} (destination exists, F13 dedupe)",
                ) from exc
            if exc.errno == errno.EXDEV:
                if destination.exists():
                    raise TaskExistsError(
                        str(task_id),
                        f"task {task_id} already picked up: refusing to move {source} -> "
                        f"{destination} (destination exists, F13 dedupe)",
                    ) from exc
                shutil.move(str(source), str(destination))
            else:
                raise
        return destination

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------
    def open_task(self, task_id: str) -> TaskDir:
        """Open the FSM handle of a picked-up task (dir must exist)."""
        _require_task_id(task_id)
        directory = self._root / task_id
        if not directory.is_dir():
            raise TaskSchemaError(str(task_id), f"unknown task {task_id}: {directory} is missing")
        return TaskDir(directory, self._cfg, bus=self._bus)

    def read_state(self, task_id: str) -> dict | None:
        """The state.json content of a picked-up task, or ``None`` when absent
        (unpicked inbox tasks have no state)."""
        _require_task_id(task_id)
        return self._read_state_file(self._root / task_id / _STATE_FILENAME)

    def list_tasks(self) -> dict[str, str]:
        """Map every picked-up task id to its FSM state (a walk of
        ``tasks/*/state.json``; the inbox is NOT included). Dirs without a
        readable state.json are warned about and skipped."""
        states: dict[str, str] = {}
        if not self._root.is_dir():
            return states
        for directory in sorted(self._root.iterdir()):
            if not directory.is_dir():
                continue
            try:
                data = self._read_state_file(directory / _STATE_FILENAME)
            except (OSError, TypeError, ValueError) as exc:
                _LOG.warning("tasks: %s state.json unreadable: %s (skipped)", directory.name, exc)
                continue
            if data is None:
                _LOG.warning("tasks: %s has no state.json; skipped", directory.name)
                continue
            state = data.get("state")
            if isinstance(state, str):
                states[directory.name] = state
            else:
                _LOG.warning("tasks: %s state.json has no state string; skipped", directory.name)
        return states

    def list_inbox(self) -> list[str]:
        """Sorted ids of dropped-but-unpicked inbox tasks (dir listing; D14)."""
        if not self._inbox.is_dir():
            return []
        return sorted(entry.name for entry in self._inbox.iterdir() if entry.is_dir())

    # ------------------------------------------------------------------
    # Dependency graph + ready-check (D15 ready; F14 guards)
    # ------------------------------------------------------------------
    def depends_graph(self) -> dict[str, list[str]]:
        """Map task id -> its ``depends_on`` ids, over ALL task.yaml files on
        disk (picked-up AND inbox; warn-and-skip unreadable ones — the
        structural input for F14 cycle checks)."""
        graph: dict[str, list[str]] = {}
        for directory, raw in self._all_task_yamls():
            deps = raw.get("depends_on")
            if isinstance(deps, list):
                graph[directory.name] = [dep for dep in deps if _is_task_id(dep)]
            elif deps is not None:
                _LOG.warning("tasks: %s task.yaml has a non-list depends_on; skipped", directory.name)
                graph[directory.name] = []
            else:
                graph[directory.name] = []
        return graph

    def parent_map(self) -> dict[str, list[str]]:
        """Map task id -> its parent task id as a single-id list (empty when
        none), over ALL task.yaml files on disk — the parent-link input for
        depth arithmetic and parent-cycle checks."""
        parents: dict[str, list[str]] = {}
        for directory, raw in self._all_task_yamls():
            parent = raw.get("parent")
            parents[directory.name] = [parent] if _is_task_id(parent) else []
        return parents

    def check_cycles(self, start_ids: Iterable[str] | None = None) -> None:
        """F14: reject cycles in the on-disk dependency graph.

        Walks BOTH edge kinds — ``depends_on`` links and parent links —
        restricted to nodes reachable from ``start_ids`` when given, over ALL
        tasks on disk otherwise (analyze-time AND boot replay).
        """
        detect_cycle(start_ids, self.depends_graph())
        detect_cycle(start_ids, self.parent_map())

    def ready_check(self, task_id: str) -> bool:
        """Ready-check (D15): does every ``depends_on`` id of ``task_id`` have
        ``state == "DONE"``?

        F14 guards run first — unknown ids raise :class:`TaskSchemaError`
        naming the missing id (an id that exists only in the inbox is known
        but simply never DONE), cycles reachable from the task raise — then
        the ready boolean. A task with no ``depends_on`` is trivially ready.
        """
        _require_task_id(task_id)
        task_yaml_path = self._root / task_id / _TASK_YAML_FILENAME
        if not task_yaml_path.is_file():
            raise TaskSchemaError(str(task_id), f"unknown task {task_id}: {task_yaml_path} missing")
        with open(task_yaml_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise TaskSchemaError(str(task_id), f"task {task_id}: task.yaml is not a mapping")
        depends = raw.get("depends_on") or []
        known = dict(self.list_tasks())
        for inbox_id in self.list_inbox():
            known.setdefault(inbox_id, "inbox")
        validate_depends(depends, known)
        self.check_cycles([task_id])
        return all(known[dep] == "DONE" for dep in depends)

    # ------------------------------------------------------------------
    # Internal (single-threaded read helpers)
    # ------------------------------------------------------------------
    def _read_state_file(self, path: Path) -> dict | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            raise TypeError(f"{path}: state.json must hold a JSON object")
        return data

    def _all_task_yamls(self) -> list[tuple[Path, dict]]:
        """Every readable (dir, raw task.yaml mapping) on disk: picked-up AND
        inbox tasks, sorted, warn-and-skip on unreadable/corrupt files."""
        entries: list[tuple[Path, dict]] = []
        for directory in sorted(self._task_yaml_dirs()):
            path = directory / _TASK_YAML_FILENAME
            if not path.is_file():
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
            except (OSError, yaml.YAMLError) as exc:
                _LOG.warning("tasks: unreadable task.yaml %s: %s (skipped)", path, exc)
                continue
            if isinstance(raw, dict):
                entries.append((directory, raw))
        return entries

    def _task_yaml_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        for base in (self._root, self._inbox):
            if base.is_dir():
                dirs.extend(entry for entry in base.iterdir() if entry.is_dir())
        return dirs
