"""Session orchestration and boot replay (ARCHITECTURE.md sections 2.4, 3.2, 3.4).

The :class:`SessionManager` wraps the per-session machinery of
:mod:`harness.session` with the process-level duties:

- **Registry (F10)** — ``self._sessions`` maps one session id to exactly ONE
  :class:`harness.session.Session` instance (and therefore one per-session
  lock) per process, so concurrent components never race on duplicates of the
  same session.
- **Get-or-create** — with a ``session_id`` the manager returns the cached
  instance or loads from disk (a missing meta.json is an honest
  :class:`ValueError` naming the id; supplied ids are validated against the
  8-char base32 alphabet as a traversal guard). With no ``session_id``, a new
  session is created — unless a ``channel_handle`` is given and another live
  (non-archived) session already owns that channel (channel mapping, section
  2.4), in which case THAT session is reused.
- **Agent-to-agent handoff (section 3.2)** — :meth:`create_child` spawns a
  child session (``parent_session``, ``depth = parent.depth + 1``, title from
  the handoff prompt) after the ``cfg.handoff.max_depth`` cap check, rejects
  over-cap handoffs with an honest :class:`ValueError` BEFORE any write, and
  records the link on BOTH transcripts (parent line ``{"child": <id>}``, child
  line ``{"parent": <id>}``), then hands the child to the router
  (``created -> routing`` via :meth:`move`).
- **Status fan-out** — transitions go through :meth:`move`, which awaits
  :meth:`Session.transition` then publishes ``session.status
  {session_id, old, new}`` over the optional bus (duck-typed: anything with
  ``async def publish(topic, payload)`` — e.g. :class:`harness.bus.Bus`).
- **Boot replay (section 3.4)** — :meth:`replay` is sync, called once at boot
  before the gateway serves. It walks ``sessions/*/transcript.jsonl`` in
  sorted order, reading each file's LAST content-bearing line via a tail read
  (O(#sessions) small reads, not a full parse). A missing/empty/unreadable
  transcript is skipped with a warning; a torn last line falls back to the
  previous good line (warning ``torn last line; previous state used``). A
  session whose last good line says ``agent_running``/``relaying`` was killed
  mid-run by a restart (F6): replay appends an honest ``note`` event
  (``data: {"reason": "interrupted by restart before completion",
  "from_state": ...}`` via the raw :class:`harness.persist.JsonlWriter`, no
  per-session lock needed at boot) and writes meta.json
  ``state = "agent_failed"`` — note-then-meta, so a second boot is idempotent.
  Counters returned: ``{"sessions", "recovered_failed", "torn_skipped"}``;
  supervisor logs the ready line via :meth:`ready_line`. A per-session
  :class:`OSError` never crashes boot — warning + continue.

The manager never rewrites transcripts (append-only contract, section 7.1)
and touches meta.json only through atomic rewrites.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.config import HarnessConfig
from harness.persist import AtomicCounter, JsonlWriter, append_text, read_jsonl
from harness.session import META_KEYS, Session, new_session_id

__all__ = ["SessionManager"]

_LOG = logging.getLogger("harness.session_manager")

_SESSION_ROOT = "sessions"
_SESSION_ID_PATTERN = re.compile(r"[A-Z2-7]{8}")
_META_FILENAME = "meta.json"
_TRANSCRIPT_FILENAME = "transcript.jsonl"

# Tail-read window (bytes) for the last-line probe; grown x4 up to file size.
_TAIL_MIN_BYTES = 4096

# Last-line states that mean "killed mid-run by a restart" (F6 honest marking).
_RECOVERED_STATES = frozenset({"agent_running", "relaying"})
_INTERRUPTED_REASON = "interrupted by restart before completion"


def _utc_now() -> str:
    """UTC clock matching :mod:`harness.session`: ISO-8601 seconds + ``Z``."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class SessionManager:
    """Owns the session fleet under ``storage.data_dir / "sessions"``:
    get-or-create semantics, handoff child sessions, optional bus fan-out and
    the boot replay. Construct with ``cfg`` and an optional bus; one manager
    per process (the in-memory registry is the single source of shared
    :class:`~harness.session.Session` instances).
    """

    def __init__(self, cfg: HarnessConfig, bus: Any | None = None) -> None:
        self._cfg = cfg
        self._bus = bus
        self._root = Path(cfg.storage.data_dir) / _SESSION_ROOT
        self._sessions: dict[str, Session] = {}

    @property
    def sessions_root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Get-or-create (section 2.4: id lookup + channel mapping)
    # ------------------------------------------------------------------
    async def get_or_create(
        self,
        session_id: str | None = None,
        channel_handle: str | None = None,
        title: str | None = None,
    ) -> Session:
        """Return the session for ``session_id`` (registry hit -> cached
        instance; else load from disk; a missing meta.json or a malformed id
        raises :class:`ValueError`), or — with ``session_id`` omitted — the
        live session owning ``channel_handle`` (section 2.4 channel mapping,
        registry first, then a sorted disk scan over ``sessions/*/meta.json``),
        else a brand-new ``created`` session logging its creation. ``title``
        only applies to freshly created sessions."""
        if session_id is not None:
            if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
                raise ValueError(
                    f"invalid session id {session_id!r}: expected 8 chars from A-Z2-7"
                )
            cached = self._sessions.get(session_id)
            if cached is not None:
                return cached
            directory = self._root / session_id
            if not (directory / _META_FILENAME).is_file():
                raise ValueError(f"unknown session id {session_id!r}: no meta.json under {directory}")
            session = Session.load(directory, self._cfg)
            self._sessions[session.id] = session
            return session
        if channel_handle is not None:
            existing = self._find_live_by_channel(channel_handle)
            if existing is not None:
                return existing
        directory = self._root / new_session_id()
        session = Session.create(
            directory, self._cfg, channel_handle=channel_handle, title=title
        )
        self._sessions[session.id] = session
        _LOG.info("created session %s (channel=%s)", session.id, channel_handle)
        return session

    def _find_live_by_channel(self, channel_handle: str) -> Session | None:
        """Registry-first, then a sorted meta.json disk scan: the first live
        (non-archived) session bound to ``channel_handle``, else ``None``.
        Unreadable metas are skipped silently (the scan is best-effort)."""
        for session in self._sessions.values():
            if not session.archived and session.channel == channel_handle:
                return session
        if not self._root.is_dir():
            return None
        for meta_path in sorted(self._root.glob(f"*/{_META_FILENAME}")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            if meta.get("channel") == channel_handle and not meta.get("archived"):
                directory = meta_path.parent
                try:
                    session = Session.load(directory, self._cfg)
                except ValueError:
                    continue
                self._sessions[session.id] = session
                return session
        return None

    # ------------------------------------------------------------------
    # Handoff children (section 3.2) + transitions with bus fan-out
    # ------------------------------------------------------------------
    async def create_child(self, parent: Session, handoff: dict) -> Session:
        """Spawn the child session for one agent-to-agent handoff.

        ``handoff`` carries at least ``prompt`` (the child's title comes from
        its first 80 chars). The depth cap is enforced FIRST — a child would
        run at ``parent.depth + 1`` and anything beyond ``cfg.handoff.max_depth``
        raises :class:`ValueError` (``handoff depth <d> exceeds max_depth <max>``)
        before any file is touched. On success: the child meta carries
        ``parent_session`` and the bumped depth, BOTH transcripts get a
        ``session_link`` line (parent: ``{"child": <id>}``, child:
        ``{"parent": <id>}``), and the child moves ``created -> routing``
        via :meth:`move` (handed to the router)."""
        child_depth = parent.depth + 1
        max_depth = self._cfg.handoff.max_depth
        if child_depth > max_depth:
            raise ValueError(f"handoff depth {child_depth} exceeds max_depth {max_depth}")
        prompt = str(handoff.get("prompt", ""))
        directory = self._root / new_session_id()
        child = Session.create(
            directory,
            self._cfg,
            title=prompt[:80],
            parent_session=parent.id,
            depth=child_depth,
        )
        self._sessions[child.id] = child
        await parent.append_event("session_link", {"child": child.id})
        await child.append_event("session_link", {"parent": parent.id})
        await self.move(child, "routing")
        return child

    async def move(self, session: Session, new_state: str, *, note: str | None = None) -> None:
        """Transition wrapper: await :meth:`Session.transition` (illegal moves
        raise before anything is published), then publish ``session.status``
        with the pre-move state."""
        old = session.state
        await session.transition(new_state, note=note)
        await self.publish_status(session, old, session.state)

    async def publish_status(self, session: Session, old: str, new: str) -> None:
        """Emit ``session.status {session_id, old, new}`` onto the bus when
        one is attached (a no-op without a bus)."""
        if self._bus is None:
            return
        payload = {"session_id": session.id, "old": old, "new": new}
        await self._bus.publish("session.status", payload)

    @staticmethod
    def ready_line(counts: dict) -> str:
        """The supervisor's INFO boot line: exactly ``harness ready - sessions=N``."""
        return f"harness ready - sessions={counts['sessions']}"

    # ------------------------------------------------------------------
    # Boot replay (section 3.4) — sync, called once before the gateway serves
    # ------------------------------------------------------------------
    def replay(self) -> dict[str, int]:
        """Recover session state from the last good transcript line of every
        session dir (sorted, deterministic walk).

        Counters: ``{"sessions": <dirs scanned>, "recovered_failed": <marked
        agent_failed>, "torn_skipped": <torn-tail fallbacks>}``. A missing
        sessions root yields all-zero counters without touching disk. Meta
        state is rebuilt from the winning line when it disagrees; a
        missing/unreadable meta.json is rebuilt minimally from the winning
        line (warning). Kill-mid-run sessions (last line ``agent_running``/
        ``relaying``) get the F6 note + ``agent_failed`` marking. Every
        per-session :class:`OSError` is warn-and-continue — boot never
        crashes on replay."""
        counts: dict[str, int] = {"sessions": 0, "recovered_failed": 0, "torn_skipped": 0}
        if not self._root.is_dir():
            return counts
        for directory in sorted(self._root.iterdir()):
            if not directory.is_dir():
                continue
            counts["sessions"] += 1
            try:
                self._replay_one(directory, counts)
            except OSError as exc:
                _LOG.warning("replay: session %s failed: %s (boot continues)", directory.name, exc)
        return counts

    def _replay_one(self, directory: Path, counts: dict[str, int]) -> None:
        transcript_path = directory / _TRANSCRIPT_FILENAME
        if not transcript_path.is_file() or transcript_path.stat().st_size == 0:
            _LOG.warning(
                "replay: session %s has a missing or empty transcript; skipping", directory.name
            )
            return
        winner, torn = self._last_good_line(transcript_path)
        if torn:
            _LOG.warning("replay: session %s torn last line; previous state used", directory.name)
            counts["torn_skipped"] += 1
        if winner is None:
            _LOG.warning(
                "replay: session %s has no usable transcript line; skipping", directory.name
            )
            return
        meta_path = directory / _META_FILENAME
        now = _utc_now()
        meta = self._read_meta(meta_path)
        if meta is None:
            meta = self._rebuild_meta(winner, directory, now)
        state = winner["state"]
        if state in _RECOVERED_STATES:
            session_id = winner.get("session_id") or directory.name
            self._ensure_trailing_newline(transcript_path)
            JsonlWriter(transcript_path).append(
                {
                    "ts": now,
                    "session_id": session_id,
                    "kind": "note",
                    "state": "agent_failed",
                    "data": {"reason": _INTERRUPTED_REASON, "from_state": state},
                }
            )
            AtomicCounter(meta_path).set({"state": "agent_failed", "updated": now})
            counts["recovered_failed"] += 1
            _LOG.warning(
                "replay: session %s interrupted mid-run in %s; marked agent_failed",
                session_id,
                state,
            )
            return
        if meta.get("state") != state:
            AtomicCounter(meta_path).set({"state": state, "updated": now})

    def _last_good_line(self, path: Path) -> tuple[dict | None, bool]:
        """Read the file's last good content-bearing line via a binary tail
        read (window starts at 4096 bytes, grows x4 up to the file size until
        a newline-terminated line shows up), scanning newlines back to front.

        Returns ``(line, torn)``: the winning line must parse as a JSON object
        carrying a string ``state``. Candidates are newline-terminated lines
        only; a trailing unterminated fragment (the torn tail of a partial
        append), an unparseable line or a state-less line is skipped in favor
        of the previous good line with the torn flag set. When the window
        yields nothing usable, the full read via
        :func:`harness.persist.read_jsonl` recovers the previous good line."""
        size = path.stat().st_size
        window = min(size, _TAIL_MIN_BYTES)
        lines: list[bytes] = []
        partial_start = False
        while True:
            start = size - window
            peek = max(0, start - 1)
            with path.open("rb") as fh:
                fh.seek(peek)
                raw = fh.read(size - peek)
            if start > 0:
                # One byte of look-behind tells a real line start from a
                # window cut mid-line (a fragment that is not a candidate).
                partial_start = not raw.startswith(b"\n")
                raw = raw[1:]
            else:
                partial_start = False
            lines = raw.splitlines(keepends=True)
            if window >= size or any(chunk.endswith(b"\n") for chunk in lines):
                break
            window = min(window * 4, size)
        winner: dict | None = None
        torn = False
        for index in range(len(lines) - 1, -1, -1):
            chunk = lines[index]
            stripped = chunk.strip()
            if not stripped:
                continue  # blank tail lines are not content-bearing
            if not chunk.endswith(b"\n"):
                torn = True  # unterminated fragment: the torn tail itself
                continue
            if index == 0 and partial_start:
                torn = True  # window cut mid-line: this fragment is mid-record
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                torn = True
                continue
            if not isinstance(record, dict) or not isinstance(record.get("state"), str):
                torn = True  # state-less lines are skipped in favor of the previous
                continue
            winner = record
            break
        if winner is None and torn:
            # Previous-line recovery: read_jsonl drops a torn final line and
            # returns the parsed prefix.
            try:
                prefix = read_jsonl(path)
            except (OSError, ValueError):
                prefix = []
            if prefix and isinstance(prefix[-1].get("state"), str):
                winner = prefix[-1]
        return winner, torn

    @staticmethod
    def _ensure_trailing_newline(path: Path) -> None:
        """Terminate a torn tail (a crash mid-append leaves the last line
        without its ``"\\n"``) before appending, so the recovery note lands on
        its own line instead of being swallowed by the fragment."""
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                append_text(path, "\n")

    @staticmethod
    def _read_meta(meta_path: Path) -> dict | None:
        """Best-effort meta.json read for replay (missing/unreadable -> None)."""
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) else None

    def _rebuild_meta(self, winner: dict, directory: Path, now: str) -> dict:
        """Rebuild a minimal (but 10-key complete) meta.json from the winning
        transcript line when the file is missing or unreadable; written
        atomically. Returns the rebuilt mapping."""
        data = winner.get("data")
        data = data if isinstance(data, dict) else {}
        session_id = winner.get("session_id") or directory.name
        meta: dict = {
            "session_id": session_id,
            "state": winner.get("state"),
            "title": data.get("title"),
            "channel": data.get("channel"),
            "agents_refs": dict(data["agents_refs"]) if isinstance(data.get("agents_refs"), dict) else {},
            "parent_session": data.get("parent_session"),
            "depth": data["depth"] if isinstance(data.get("depth"), int) else 0,
            "created": now,
            "updated": now,
            "archived": bool(data.get("archived")),
        }
        AtomicCounter(directory / _META_FILENAME).set({key: meta[key] for key in META_KEYS})
        _LOG.warning(
            "replay: session %s meta.json missing or unreadable; minimal meta rebuilt", session_id
        )
        return meta
