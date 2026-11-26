"""Memory engine: budgeted MEMORY_EDIT application over curated markdown files.

Implements ARCHITECTURE.md section 2.5 (Hermes' two-file model) plus the
approved R3 breakdown section 4 and DECISIONS.md D4. The engine owns three
curatable files under ``<storage.data_dir>/memory/`` — ``MEMORY.md``,
``USER.md`` and ``lessons.md`` (the :data:`_FILES` registry keys each file
name to its budget key in ``cfg.memory.budgets``). Memory files are CURATED
content, not append-only logs: budgets are enforced at write time and every
rewrite is a full-content operation.

Since the pydantic-ai v2 migration (plan D4) the THREE canonical files live
in ONE :class:`pydantic_ai_harness.memory.FileMemoryStore` shared with the
harness ``Memory`` capability: the store is rooted at the same
``<storage.data_dir>/memory`` directory
(``FileMemoryStore('.', workspace=LocalWorkspaceBackend(...))`` — the store
requires an explicit ``workspace=`` outside a ``Memory`` run, else
``UserError``). Writes go through :meth:`MemoryEngine._persist_file` /
:meth:`MemoryEngine._append_file` (read-observe-then-write with the store's
SHA-256 content versions for torn-tail/staged-replace safety), and reads
through :meth:`MemoryEngine._read_store`; every byte that reached disk before
the migration still reaches disk byte-identically. Engine-side policy is
unchanged: fenced-edit parsing, audit rows and digest projections stay here,
and the router/gateway keep consuming :meth:`MemoryEngine.digest` — no store
imports outside this module. Fenced edits arriving from a RUNNING event loop
are applied on a private daemon-thread loop (:func:`_run_coro`), so the
engine's synchronous surface is unchanged for every caller.

Agents self-maintain memory by emitting fenced ``MEMORY_EDIT`` blocks
(parsed by :mod:`bob_harness.blocks`); this module validates and applies them
with Hermes' memory-tool semantics:

- ``op: add`` — append one bullet line ("- <content>"); a re-apply of an
  already-present exact bullet is an idempotent no-op (``reason "duplicate"``,
  file untouched, no bus event).
- ``op: replace`` — the FIRST line containing ``pattern`` (substring) is
  replaced by ``content`` (a ``str`` possibly spanning lines, or a
  ``list[str]`` of lines); no match rejects with ``"pattern not found"``.
- ``op: remove`` — the FIRST line containing ``pattern`` is deleted; no
  match rejects with ``"pattern not found"``.

Budget (D4 — no auto-compaction, NO silent truncation): before any write the
post-edit text must fit the file's char budget. Overflow appends an audit
line (``ok=false``, the error text as ``reason``), leaves the file untouched
and THEN raises :class:`~bob_harness.errors.MemoryBudgetError` — an honest
rejection that forces the agent to consolidate.

Rejected edits (unparseable block, unknown op/file/field, missing fields,
pattern not found) change nothing on disk; every ATTEMPTED edit — applied,
duplicate, rejected or overflow — appends one audit line to
``<data_dir>/state/memory_audit.jsonl`` via
:class:`bob_harness.persist.JsonlWriter` with ``"unknown"`` fallbacks for
``op``/``file``/sizes when the block body is unparseable. The ``file`` audit
field carries the registry KEY ("MEMORY"/"USER"/"lessons"), the error
messages name the on-disk filename.

Successful, content-changing applies emit a best-effort ``memory.updated``
bus event (``{file, op}``) through the private :func:`_emit` helper: with no
running event loop (or no bus) it is a silent no-op; bus failures inside the
fired task never propagate back into :meth:`apply_edit`. The helper is
mirrored in :mod:`bob_harness.skills` (same shape, per the R3 breakdown).

``digest`` is the router-prompt projection (read-only, never written): up to
6 bullet lines, each clamped to 100 chars, joined with newlines, whole
result hard-truncated to 600 chars. ``load`` always returns the FULL file —
budgets are a write-time concept, reads are never truncated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bob_harness.blocks import Block
from bob_harness.config import HarnessConfig
from bob_harness.errors import MemoryBudgetError
from bob_harness.persist import JsonlWriter

__all__ = [
    "ApplyResult",
    "MemoryEngine",
]

# Module logger (pattern: bob_harness.session_manager).
_LOG = logging.getLogger("bob_harness.memory")

# D4: the shared FileMemoryStore's canonical store paths (registry-KEY-relative
# names below the store root) for the three curatable files.
_STORE_PATHS: dict[str, str] = {
    "MEMORY": "MEMORY.md",
    "USER": "USER.md",
    "lessons": "lessons.md",
}

# Registry key -> (filename, budget key in cfg.memory.budgets); module constant.
_FILES: dict[str, tuple[str, str]] = {
    "MEMORY": ("MEMORY.md", "memory_chars"),
    "USER": ("USER.md", "user_chars"),
    "lessons": ("lessons.md", "lessons_chars"),
}

# MEMORY_EDIT schema: op -> (all schema keys, op-specific required key -> reason).
_OP_KEYS: dict[str, set[str]] = {
    "add": {"op", "file", "content"},
    "replace": {"op", "file", "pattern", "content"},
    "remove": {"op", "file", "pattern"},
}

_DIGEST_BULLET_TAKE = 6  # digest: take this many bullet (fallback: non-empty) lines
_DIGEST_LINE_CLAMP = 100  # digest: clamp each taken line to this many chars
_DIGEST_MAX_CHARS = 600  # digest: hard-truncate the joined projection to this many chars


def _utc_now() -> str:
    """UTC clock matching :mod:`bob_harness.session`: ISO-8601 seconds + ``Z``."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(bus: Any | None, topic: str, payload: dict) -> None:
    """Best-effort bus emission, same shape in :mod:`bob_harness.skills`.

    Fire-and-forget into the RUNNING event loop only; with no bus and/or no
    running loop (``RuntimeError``) this is a silent no-op — the sync apply
    path never blocks on (or creates) a loop. Bus failures inside the fired
    task are swallowed and logged so they never propagate into the caller.
    """
    if bus is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _publish() -> None:
        try:
            await bus.publish(topic, dict(payload))
        except Exception as exc:  # noqa: BLE001 — bus failures must not escape
            _LOG.warning("bus publish failed for %s: %s", topic, exc)

    loop.create_task(_publish())


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically rewrite ``path`` with ``text`` (documented AtomicCounter
    pattern: write ``<path>.tmp`` -> fsync -> ``os.replace`` -> fsync the
    parent directory fd, tolerating directory-fsync-less filesystems)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    data = text.encode("utf-8")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # not every filesystem supports directory fsync
    finally:
        os.close(dir_fd)


def _run_coro(coro: Any) -> Any:
    """Run ``coro`` to completion from SYNC context on a private daemon-thread
    event loop (the engine's sync surface never blocks a foreign loop and the
    thread never keeps the process alive)."""
    if threading.current_thread().name == "bob-harness-memory-store":
        raise RuntimeError("re-entrant memory store I/O on the private store loop")
    outcome: list[tuple[bool, Any]] = []
    done = threading.Event()

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            outcome.append((True, loop.run_until_complete(coro)))
        except BaseException as exc:  # noqa: BLE001 — relayed to the caller below
            outcome.append((False, exc))
        finally:
            with suppress(BaseException):  # best-effort loop teardown
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            done.set()

    thread = threading.Thread(
        target=_worker, name="bob-harness-memory-store", daemon=True
    )
    thread.start()
    done.wait()
    ok, value = outcome[0]
    if not ok:
        raise value
    return value


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one attempted ``MEMORY_EDIT`` application.

    Attributes:
        ok: whether the edit was applied (or idempotently already present).
        file: the registry key ("MEMORY"/"USER"/"lessons"), or "unknown"
            when the block body was unparseable.
        op: the attempted op ("add"/"replace"/"remove"), or "unknown".
        before_len: file length before the edit (0 when nothing was loaded,
            e.g. schema rejection; the real size for audits of true attempts).
        after_len: file length after the edit (equals ``before_len`` for
            no-ops/rejections; the rejected post size for budget overflow),
            0 when nothing was loaded.
        reason: rejection/stop reason, or ``None`` on success.
    """

    ok: bool
    file: str
    op: str
    before_len: int
    after_len: int
    reason: str | None = None


class MemoryEngine:
    """Applies budgeted ``MEMORY_EDIT`` blocks to the curated memory files.

    One engine per process; constructed with the harness configuration (the
    single source of budgets via ``cfg.memory.budgets``) and an optional bus
    for ``memory.updated`` fan-out. All mutating operations are synchronous
    and each records exactly one audit line in
    ``<data_dir>/state/memory_audit.jsonl``.
    """

    def __init__(self, cfg: HarnessConfig, bus: Any | None = None) -> None:
        self._cfg = cfg
        self._bus = bus
        self._root = Path(cfg.storage.data_dir) / "memory"
        self._audit_writer = JsonlWriter(
            Path(cfg.storage.data_dir) / "state" / "memory_audit.jsonl"
        )
        # D4: ONE FileMemoryStore shared with the harness ``Memory`` capability.
        # The engine keeps the canonical three files inside the SAME store
        # root; store I/O routes through :meth:`_persist_file`/:meth:`_read`
        # (sync wrappers over the async store via :func:`_run_coro`). The
        # capability's own ``workspace`` binding does not apply here: the
        # engine binds the directory directly with ``LocalWorkspaceBackend``
        # (verified contract: ``FileMemoryStore(dir)`` outside a Memory run
        # needs ``workspace=``, else ``UserError``).
        from pydantic_ai.workspaces import LocalWorkspaceBackend
        from pydantic_ai_harness.memory import FileMemoryStore

        self._root.mkdir(parents=True, exist_ok=True)
        self._store = FileMemoryStore(
            ".", workspace=LocalWorkspaceBackend(self._root)
        )

    # ------------------------------------------------------------------
    # Reading (never truncated — budgets apply to writes only)
    # ------------------------------------------------------------------
    def _resolve(self, file: str) -> tuple[Path, str]:
        """Resolve a registry key to ``(file path, budget key)``; unknown key
        raises :class:`ValueError`."""
        try:
            filename, budget_key = _FILES[file]
        except KeyError:
            raise ValueError(f"unknown file '{file}' (see bob_harness.memory._FILES)") from None
        return self._root / filename, budget_key

    def _path(self, file: str) -> Path:
        """Resolve a registry key to its file path; unknown key -> ValueError."""
        return self._resolve(file)[0]

    def _budget(self, file: str) -> int:
        """Char budget of ``file`` from ``cfg.memory.budgets``."""
        return int(getattr(self._cfg.memory.budgets, self._resolve(file)[1]))

    def _store_path(self, file: str) -> str:
        """Canonical store path for a registry key; unknown key -> ValueError."""
        if file not in _STORE_PATHS:
            raise ValueError(f"unknown file '{file}' (see bob_harness.memory._FILES)")
        return _STORE_PATHS[file]

    def _read_store(self, file: str) -> str:
        """Whole-file read through the shared store (``""`` when missing).

        The store reports truncation via ``MemoryFile.truncated``; the engine
        always passes unlimited budgets so reads are never truncated (budgets
        apply to writes only — module contract).
        """

        async def _go() -> Any:
            return await self._store.read(self._store_path(file), max_chars=1_000_000)

        mfile = _run_coro(_go())
        return "" if mfile is None else mfile.content

    def _persist_file(self, file: str, text: str) -> str:
        """Durably rewrite a canonical file THROUGH the shared store.

        The torn-tail safety and staged-replace durability of the old
        ``_atomic_write_text`` come from the store's staged ``mv`` (verified
        0.73.0). Read-observe-then-write: ``expected_version`` is the version
        observed in the SAME store call chain. Returns the store-reported
        version (SHA-256 hex of ``text``).
        """

        async def _go() -> str:
            current = await self._store.read(self._store_path(file), max_chars=1_000_000)
            expected = None if current is None else current.version
            mutation = await self._store.write(
                self._store_path(file), text, expected_version=expected
            )
            assert mutation.version is not None  # non-None content -> non-None version
            return mutation.version

        return _run_coro(_go())

    def _append_file(self, file: str, delta: str) -> str:
        """Durably APPEND ``delta`` to a canonical file through the shared store.

        The store has no append primitive, so this is the read-merge-write
        rewrite that preserves the historical append byte-contract (the result
        is byte-identical to ``append_text``; races are serialized by the
        store's internal lock — one-writer-per-directory as before). Returns
        the store-reported post-write version.
        """

        async def _go() -> str:
            current = await self._store.read(self._store_path(file), max_chars=1_000_000)
            before = "" if current is None else current.content
            expected = None if current is None else current.version
            mutation = await self._store.write(
                self._store_path(file), before + delta, expected_version=expected
            )
            assert mutation.version is not None
            return mutation.version

        return _run_coro(_go())

    def load(self, file: str) -> str:
        """Return the whole file text (``""`` when the file is missing).

        Unknown registry keys raise :class:`ValueError`; a missing file means
        empty memory, not an error.
        """
        try:
            return self._read_store(file)
        except ValueError:
            raise
        except Exception:  # noqa: BLE001 - store-unavailable fallback (parity with v1)
            _LOG.warning("memory store read failed; falling back to plain file read")
            try:
                return self._path(file).read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""

    def fits(self, file: str, content: str) -> bool:
        """Whether ``content`` fits ``file``'s char budget (D4)."""
        return len(content) <= self._budget(file)

    def digest(self, file: str) -> str:
        """Build the <=600-char router-prompt projection of ``file``.

        Takes the first up-to-6 bullet lines (``"- "`` or ``"* "``; when the
        file has no bullets, the first up-to-6 non-empty lines), clamps each
        taken line to 100 chars, joins the lines with ``"\\n"`` and
        hard-truncates the result to 600 chars. Read-only projection —
        never written anywhere — returning ``""`` for an empty/missing file.
        """
        lines = self.load(file).splitlines()
        bullets = [line for line in lines if line.startswith(("- ", "* "))]
        taken = bullets[:_DIGEST_BULLET_TAKE]
        if not taken:
            taken = [line for line in lines if line.strip()][: _DIGEST_BULLET_TAKE]
        text = "\n".join(line[:_DIGEST_LINE_CLAMP] for line in taken)
        if len(text) > _DIGEST_MAX_CHARS:
            text = text[:_DIGEST_MAX_CHARS]
        return text

    # ------------------------------------------------------------------
    # Applying MEMORY_EDIT blocks
    # ------------------------------------------------------------------
    def apply_edit(self, block: Block) -> ApplyResult:
        """Validate and apply one ``MEMORY_EDIT`` block (line-oriented).

        Any attempted edit records exactly one audit line. Rejections return
        a failed :class:`ApplyResult` (the file is untouched); budget
        overflow records its audit line first, the file stays untouched and
        then :class:`MemoryBudgetError` propagates.
        """
        if block.tag != "MEMORY_EDIT":
            raise ValueError("apply_edit expects a MEMORY_EDIT block")
        if not block.ok or not isinstance(block.fields, dict):
            # Unparseable block: op/file/sizes fall back to "unknown"/0 honestly.
            reason = block.reason if block.reason is not None else "unparseable block"
            self._audit("unknown", "unknown", False, 0, 0, reason)
            return ApplyResult(False, "unknown", "unknown", 0, 0, reason)

        fields = block.fields
        op = fields.get("op")
        file = fields.get("file")
        op_audit = op if isinstance(op, str) else "unknown"
        file_audit = file if isinstance(file, str) else "unknown"

        if not isinstance(op, str) or op not in _OP_KEYS:
            reason = f"unknown op '{op}'"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        if not isinstance(file, str) or file not in _FILES:
            reason = f"unknown file '{file}'"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        unknown = set(fields) - _OP_KEYS[op]
        if unknown:
            reason = f"unknown field '{min(unknown)}'"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)

        if op == "add":
            return self._apply_add(file, file_audit, op_audit, fields.get("content"))
        if op == "replace":
            return self._apply_replace(file, file_audit, op_audit, fields.get("pattern"),
                                       fields.get("content"))
        return self._apply_remove(file, file_audit, op_audit, fields.get("pattern"))

    # -- op: add -------------------------------------------------------
    def _apply_add(self, file: str, file_audit: str, op_audit: str, content: Any) -> ApplyResult:
        """Append one bullet; an exact duplicate line is an idempotent no-op."""
        if not isinstance(content, str) or content == "":
            reason = "missing content"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        before = self.load(file)
        bullet = f"- {content}"
        if bullet in before.split("\n"):
            # Idempotent re-apply: audit (ok, sizes equal, reason) but no write,
            # no memory.updated (content did not change — R3 variance).
            self._audit(file_audit, op_audit, True, len(before), len(before), "duplicate")
            return ApplyResult(True, file, "add", len(before), len(before), "duplicate")
        # Add historically was a pure append (persist.py append contract); on
        # the shared store it is the byte-identical read-merge-write (D4).
        boundary = "" if (not before or before.endswith("\n")) else "\n"
        delta = boundary + bullet + "\n"
        post_len = len(before) + len(delta)
        budget = self._budget(file)
        if post_len > budget:
            self._audit_overflow(file_audit, op_audit, len(before), post_len)
            raise MemoryBudgetError(_FILES[file][0], budget, post_len)
        self._append_file(file, delta)
        self._audit(file_audit, op_audit, True, len(before), post_len, None)
        _emit(self._bus, "memory.updated", {"file": file, "op": "add"})
        return ApplyResult(True, file, "add", len(before), post_len, None)

    # -- op: replace / remove (single rewrite through the shared store) --
    def _apply_replace(self, file: str, file_audit: str, op_audit: str, pattern: Any,
                       content: Any) -> ApplyResult:
        """Replace the FIRST pattern-containing line by ``content`` (multi-line)."""
        if not isinstance(pattern, str) or pattern == "":
            reason = "missing pattern"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        content_lines = self._content_lines(content)
        if content_lines is None:
            reason = "missing content"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        before = self.load(file)
        lines = before.split("\n")
        index = next((i for i, line in enumerate(lines) if pattern in line), None)
        if index is None:
            reason = "pattern not found"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        post = "\n".join(lines[:index] + content_lines + lines[index + 1:])
        return self._commit(file, file_audit, op_audit, "replace", before, post)

    def _apply_remove(self, file: str, file_audit: str, op_audit: str, pattern: Any) -> ApplyResult:
        """Delete the FIRST pattern-containing line."""
        if not isinstance(pattern, str) or pattern == "":
            reason = "missing pattern"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        before = self.load(file)
        lines = before.split("\n")
        index = next((i for i, line in enumerate(lines) if pattern in line), None)
        if index is None:
            reason = "pattern not found"
            self._audit(file_audit, op_audit, False, 0, 0, reason)
            return ApplyResult(False, file_audit, op_audit, 0, 0, reason)
        post = "\n".join(lines[:index] + lines[index + 1:])
        return self._commit(file, file_audit, op_audit, "remove", before, post)

    @staticmethod
    def _content_lines(content: Any) -> list[str] | None:
        """Normalize replace ``content`` (``str`` or ``list[str]``) to lines."""
        if isinstance(content, str):
            return content.split("\n")
        if isinstance(content, list) and all(isinstance(item, str) for item in content):
            return content
        return None

    # -- shared commit / audit plumbing --------------------------------
    def _commit(self, file: str, file_audit: str, op_audit: str, op: str, before: str,
                post: str) -> ApplyResult:
        """Budget-check, store-persist, audit and emit for a rewrite op."""
        after_len = len(post)
        if len(post) > self._budget(file):
            self._audit_overflow(file_audit, op_audit, len(before), after_len)
            raise MemoryBudgetError(_FILES[file][0], self._budget(file), after_len)
        self._persist_file(file, post)
        self._audit(file_audit, op_audit, True, len(before), after_len, None)
        _emit(self._bus, "memory.updated", {"file": file, "op": op})
        return ApplyResult(True, file, op, len(before), after_len, None)

    def _audit(self, file: str, op: str, ok: bool, before_len: int, after_len: int,
               reason: str | None) -> None:
        """Append one audit line; ``reason`` is only present when not ``None``."""
        record: dict[str, Any] = {
            "ts": _utc_now(),
            "file": file,
            "op": op,
            "ok": ok,
            "before_len": before_len,
            "after_len": after_len,
        }
        if reason is not None:
            record["reason"] = reason
        self._audit_writer.append(record)

    def _audit_overflow(self, file: str, op: str, before_len: int, post_len: int) -> None:
        """Record a skipped write: ok=false with the honest error text as reason."""
        filename, budget_key = _FILES[file]
        error = MemoryBudgetError(filename, int(getattr(self._cfg.memory.budgets, budget_key)),
                                  post_len)
        self._audit(file, op, False, before_len, post_len, str(error))

    def apply_blocks(self, blocks: list[Block]) -> list[ApplyResult]:
        """Apply every ``MEMORY_EDIT`` block in order; other tags are skipped
        (no result, no audit). A :class:`MemoryBudgetError` propagates after
        its audit line — earlier edits in the list stay applied."""
        return [self.apply_edit(block) for block in blocks if block.tag == "MEMORY_EDIT"]
