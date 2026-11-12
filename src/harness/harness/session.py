"""Conversation sessions: the per-session state machine and its on-disk files.

Implements the session contract of ARCHITECTURE.md sections 2.4, 3.2 and 7.1
(see the approved R2 breakdown): one session is one conversation thread stored
under ``storage.data_dir / "sessions" / <session_id>`` with exactly two files.

- ``transcript.jsonl`` — append-only JSONL, never rewritten, written through
  :class:`harness.persist.JsonlWriter` (line-buffered, fsync-flushed). Every
  state transition and every bus-visible event becomes one ``\\n``-terminated
  line, so a crash loses at most the trailing line (torn-tail tolerated by
  readers — see :func:`harness.persist.read_jsonl` and the boot replay in
  :mod:`harness.session_manager`).
- ``meta.json`` — the 10-key rewrite-ok header ``{session_id, state, title,
  channel, agents_refs, parent_session, depth, created, updated, archived}``,
  atomically rewritten through :class:`harness.persist.AtomicCounter` (tmp +
  fsync + ``os.replace`` + directory fsync) on every state change, rename,
  agent-ref update and archive. ``created`` never changes after creation;
  ``updated`` refreshes on every meta write.

State machine (module constants ``STATES`` and ``TRANSITIONS``; section 2.4):

    created -> routing             routing -> agent_running
    agent_running -> relaying      relaying -> idle
    idle -> routing (new turn)     idle -> archived (terminal, 30-day duty)
    routing/agent_running/relaying -> failed
    failed -> routing (resumable by a new user msg)

The boot-recovery state ``agent_failed`` is deliberately absent: replay writes
it directly into meta.json (never a runtime transition — see
:mod:`harness.session_manager`). Transitions are validated against the
table; an illegal move raises :class:`harness.errors.SessionFSMError`, logs a
warning, and leaves state, transcript and meta untouched.

Transcript line shape: ``{"ts", "session_id", "kind", "state"}`` plus an
optional ``data`` mapping. Every line the session writes is stamped with the
session's CURRENT state (makes the replay last-line read sound) and ``ts`` is
ISO-8601 UTC with seconds precision and a ``Z`` suffix, e.g.
``2026-11-12T10:00:00Z``. Event kinds are the closed ``EVENTS`` registry;
unknown kinds raise :class:`ValueError` before anything is written.

Concurrency (F10): every append and every meta rewrite is serialized behind a
per-session :class:`asyncio.Lock`, so 20 concurrent ``append_event`` tasks on
one session produce 20 intact JSON lines — never torn or interleaved.
``rename`` and ``archive`` are intentionally sync (boot-time duties run before
or without a loop); they lean on the thread-locks inside the persist
primitives for file-level atomicity.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from harness.config import HarnessConfig
from harness.errors import SessionFSMError
from harness.persist import AtomicCounter, JsonlWriter

__all__ = [
    "EVENTS",
    "META_KEYS",
    "STATES",
    "TRANSITIONS",
    "Session",
    "new_session_id",
]

_LOG = logging.getLogger("harness.session")

_META_FILENAME = "meta.json"
_TRANSCRIPT_FILENAME = "transcript.jsonl"

# Session ids: 8-char base32 (A-Z2-7), the dir name below sessions/.
_ID_PATTERN = re.compile(r"[A-Z2-7]{8}")

# Session state machine (ARCHITECTURE.md section 2.4; decision 3 table). The
# boot state agent_failed is written by replay only, never transitioned.
TRANSITIONS: dict[str, set[str]] = {
    "created": {"routing"},
    "routing": {"agent_running", "failed"},
    "agent_running": {"relaying", "failed"},
    "relaying": {"idle", "failed"},
    "idle": {"routing", "archived"},
    "failed": {"routing"},
    "archived": set(),
}
STATES: set[str] = set(TRANSITIONS) | {
    target for targets in TRANSITIONS.values() for target in targets
}

# Transcript event kinds; unknown kinds raise ValueError (decision 4).
EVENTS: set[str] = {
    "user",
    "router",
    "agent_chunk",
    "agent_final",
    "system",
    "note",
    "session_link",
    "state_change",
    "handoff",
}

# The 10 meta.json keys, in their contract order (rewrite-ok header).
META_KEYS = (
    "session_id",
    "state",
    "title",
    "channel",
    "agents_refs",
    "parent_session",
    "depth",
    "created",
    "updated",
    "archived",
)


def _now() -> str:
    """UTC clock for transcript ``ts`` and meta ``created``/``updated``:
    ISO-8601 with seconds precision and a ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_session_id() -> str:
    """Fresh 8-char base32 session id (alphabet A-Z2-7, no padding): the
    base32 encoding of 5 random bytes."""
    return base64.b32encode(secrets.token_bytes(5)).decode().rstrip("=")


def _normalize_meta(data: dict, session_dir: Path) -> dict:
    """Fill missing/ill-typed meta keys with birth defaults so a Session always
    carries the full 10-key header (``session_id`` falls back to the dir name,
    which replay-guaranteed metas share)."""
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = session_dir.name
    created = data.get("created")
    if not isinstance(created, str):
        created = _now()
    updated = data.get("updated")
    if not isinstance(updated, str):
        updated = created
    agents_refs = data.get("agents_refs")
    depth = data.get("depth")
    defaults = {
        "session_id": session_id,
        "state": data["state"] if isinstance(data.get("state"), str) else "created",
        "title": data.get("title"),
        "channel": data.get("channel"),
        "agents_refs": dict(agents_refs) if isinstance(agents_refs, dict) else {},
        "parent_session": data.get("parent_session"),
        "depth": depth if isinstance(depth, int) and not isinstance(depth, bool) else 0,
        "created": created,
        "updated": updated,
        "archived": bool(data.get("archived")),
    }
    return {key: defaults[key] for key in META_KEYS}


class Session:
    """One conversation thread: the FSM plus its append-only transcript and
    atomically rewritten meta.json. Not a frozen dataclass — the class owns
    its I/O through :class:`harness.persist.JsonlWriter` (transcript appends)
    and :class:`harness.persist.AtomicCounter` (meta rewrites), serialized by
    a per-instance :class:`asyncio.Lock`. Construct via :meth:`create` (fresh
    session dir) or :meth:`load` (reload from meta.json).

    Attributes:
        id: 8-char base32 session id (also the directory name).
        state: current FSM state (one of :data:`STATES`).
        title: human title or ``None`` until renamed.
        channel: channel handle the session is bound to (channel mapping,
            section 2.4) or ``None``.
        agents_refs: per-agent session refs (hermes/opencode/router ids),
            reused across turns.
        parent_session: parent session id for handoff children, else ``None``.
        depth: handoff depth (0 for roots; children are parent+1, capped by
            ``cfg.handoff.max_depth`` in the manager).
        archived: whether the session is archived (terminal state).
        created/updated: meta timestamps (ISO-8601 UTC, ``Z`` suffix);
            ``created`` is immutable, ``updated`` refreshes on every meta write.
    """

    def __init__(
        self,
        session_dir: Path,
        cfg: HarnessConfig,
        transcript: JsonlWriter,
        meta: AtomicCounter | None = None,
    ) -> None:
        self._session_dir = Path(session_dir)
        self._cfg = cfg
        self._transcript = transcript
        if meta is None:
            meta = AtomicCounter(self._session_dir / _META_FILENAME)
        self._counter = meta
        self._data: dict = _normalize_meta(dict(self._counter.load()), self._session_dir)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        session_dir: Path | str,
        cfg: HarnessConfig,
        *,
        session_id: str | None = None,
        channel_handle: str | None = None,
        title: str | None = None,
        parent_session: str | None = None,
        depth: int = 0,
    ) -> Session:
        """Initialize a fresh session directory: mkdir, an empty (but present)
        ``transcript.jsonl``, and an atomically written 10-key ``meta.json``
        with ``state="created"``. The id defaults to the directory name (the
        manager always names it with :func:`new_session_id`); an invalid id
        raises :class:`ValueError`. ``created``/``updated`` start at now."""
        directory = Path(session_dir)
        directory.mkdir(parents=True, exist_ok=True)
        session_id = session_id if session_id is not None else directory.name
        if _ID_PATTERN.fullmatch(session_id) is None:
            raise ValueError(
                f"invalid session id {session_id!r}: expected 8 chars from A-Z2-7"
            )
        transcript_path = directory / _TRANSCRIPT_FILENAME
        transcript_path.touch(exist_ok=True)
        now = _now()
        meta_map: dict = {
            "session_id": session_id,
            "state": "created",
            "title": title,
            "channel": channel_handle,
            "agents_refs": {},
            "parent_session": parent_session,
            "depth": depth,
            "created": now,
            "updated": now,
            "archived": False,
        }
        counter = AtomicCounter(directory / _META_FILENAME)
        counter.set(meta_map)
        session = cls(directory, cfg, JsonlWriter(transcript_path), meta=counter)
        _LOG.info(
            "session %s created (channel=%s parent=%s depth=%d)",
            session_id,
            channel_handle,
            parent_session,
            depth,
        )
        return session

    @classmethod
    def load(cls, session_dir: Path | str, cfg: HarnessConfig) -> Session:
        """Reload an existing session from its 10-key ``meta.json`` (missing
        or unreadable meta raises :class:`ValueError`; a non-mapping meta
        raises :class:`TypeError`). The transcript writer is re-attached
        lazily; a not-yet-materialized transcript file is fine."""
        directory = Path(session_dir)
        meta_path = directory / _META_FILENAME
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"session {directory.name}: no meta.json under {directory}") from None
        except (OSError, ValueError) as exc:
            raise ValueError(f"session {directory.name}: meta.json unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise TypeError(f"session {directory.name}: meta.json must hold a JSON object")
        return cls(directory, cfg, JsonlWriter(directory / _TRANSCRIPT_FILENAME))

    # ------------------------------------------------------------------
    # Read-only view
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        return str(self._data["session_id"])

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    @property
    def transcript_path(self) -> Path:
        return self._session_dir / _TRANSCRIPT_FILENAME

    @property
    def meta_path(self) -> Path:
        return self._session_dir / _META_FILENAME

    @property
    def state(self) -> str:
        return str(self._data["state"])

    @property
    def title(self) -> str | None:
        return self._data["title"]

    @property
    def channel(self) -> str | None:
        return self._data["channel"]

    @property
    def agents_refs(self) -> dict[str, str]:
        return dict(self._data["agents_refs"])

    @property
    def parent_session(self) -> str | None:
        return self._data["parent_session"]

    @property
    def depth(self) -> int:
        return int(self._data["depth"])

    @property
    def archived(self) -> bool:
        return bool(self._data["archived"])

    @property
    def created(self) -> str:
        return str(self._data["created"])

    @property
    def updated(self) -> str:
        return str(self._data["updated"])

    # ------------------------------------------------------------------
    # FSM + transcript writes (async: serialized behind the per-session lock)
    # ------------------------------------------------------------------
    async def transition(self, new_state: str, *, note: str | None = None) -> None:
        """Validate ``new_state`` against :data:`TRANSITIONS` and move the FSM.

        An illegal move logs a warning and raises
        :class:`~harness.errors.SessionFSMError` leaving state, transcript and
        meta untouched. A legal move — under the per-session lock — appends
        one ``state_change`` line
        (``{"kind": "state_change", "state": <new>, "data": {"from": <old>, ...}}``)
        and atomically rewrites meta.json (``state``, ``updated``; also
        ``archived`` is set true for the terminal archive move)."""
        old = self.state
        if new_state not in TRANSITIONS.get(old, set()):
            _LOG.warning("session %s: illegal transition %s -> %s", self.id, old, new_state)
            raise SessionFSMError(self.id, old, new_state)
        async with self._lock:
            self._data["state"] = new_state
            payload: dict = {"from": old}
            if new_state == "archived":
                self._data["archived"] = True
                payload["archived"] = True
            if note is not None:
                payload["note"] = note
            self._append_line("state_change", payload, state=new_state)
            self._write_meta()

    async def append_event(
        self, kind: str, data: dict | None = None, *, state: str | None = None
    ) -> None:
        """Append one event line to the transcript under the per-session lock.

        The line is ``{"ts", "session_id", "kind", "state"}`` (+ ``data`` when
        given), with ``state`` defaulting to the CURRENT session state. Unknown
        kinds raise :class:`ValueError` before any write."""
        if kind not in EVENTS:
            raise ValueError(
                f"unknown transcript event kind {kind!r}; expected one of {sorted(EVENTS)}"
            )
        async with self._lock:
            self._append_line(kind, data, state=state)

    async def set_agent_ref(self, agent: str, ref: str) -> None:
        """Record one per-agent session ref (multi-turn continuity) and
        rewrite meta.json under the per-session lock."""
        async with self._lock:
            refs = dict(self._data["agents_refs"])
            refs[agent] = ref
            self._data["agents_refs"] = refs
            self._write_meta()

    # ------------------------------------------------------------------
    # Sync mutators (boot duties; file-level atomicity via persist primitives)
    # ------------------------------------------------------------------
    def rename(self, title: str) -> None:
        """Set a human title and atomically rewrite meta.json (sync)."""
        self._data["title"] = title
        self._write_meta()

    def archive(self) -> None:
        """``idle -> archived`` terminal move: meta ``archived=true`` plus a
        final ``state_change`` line with ``data={"from": "idle", "archived":
        true}`` (sync; boot-time 30-day duty). Any other source state logs a
        warning and raises :class:`~harness.errors.SessionFSMError`."""
        old = self.state
        if "archived" not in TRANSITIONS.get(old, set()):
            _LOG.warning("session %s: illegal transition %s -> archived", self.id, old)
            raise SessionFSMError(self.id, old, "archived")
        self._data["state"] = "archived"
        self._data["archived"] = True
        self._append_line("state_change", {"from": old, "archived": True}, state="archived")
        self._write_meta()

    # ------------------------------------------------------------------
    # Internal, non-reentrant helpers (callers hold the invariants)
    # ------------------------------------------------------------------
    def _append_line(self, kind: str, data: dict | None, *, state: str | None = None) -> None:
        """Append one transcript line; the caller already owns serialization
        (the asyncio.Lock in the async paths, or single-threaded boot duties
        otherwise). Does not touch meta.json."""
        line: dict = {
            "ts": _now(),
            "session_id": self.id,
            "kind": kind,
            "state": state if state is not None else self.state,
        }
        if data is not None:
            line["data"] = data
        self._transcript.append(line)

    def _write_meta(self) -> None:
        """Atomically rewrite the full 10-key meta mapping; ``updated``
        refreshes, ``created`` (and everything else in ``_data``) is written
        unchanged."""
        self._data["updated"] = _now()
        self._counter.set(dict(self._data))
