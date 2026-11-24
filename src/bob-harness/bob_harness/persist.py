"""Durable persistence primitives for the harness.

Implements the storage contract of ARCHITECTURE.md section 7.1 (see
``docs/tasks/19-rework-orchestrator-harness-poc.md``) and design point P1.3:

- Append-only JSONL files are never rewritten: writers only append
  ``\\n``-terminated lines (single-object-per-line JSON) in binary append
  mode. Existing content is never touched.
- The last write loses at most the final partial line: a crash mid-append
  tears only the trailing line, so readers are torn-line tolerant and silently
  ignore an unparseable last line, returning the parsed prefix. A parse
  failure on any non-final line is corruption, not a torn tail, and is raised.
- Appends are line-buffered and fsync-flushed: every append writes, flushes,
  and ``os.fsync``-s the file descriptor before returning, so an acknowledged
  append survives a crash or power loss.
- The ``budgets.json`` counter is the only rewritten file: the whole mapping
  is written to ``<path>.tmp``, fsynced, installed atomically via
  ``os.replace``, and the parent directory fd is fsynced so the rename itself
  is durable.

All functions and classes accept plain filesystem paths (``str`` or
``os.PathLike``); :mod:`pathlib` is used internally. Parent directories are
created lazily wherever a write happens — never by the read path.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path

__all__ = [
    "AtomicCounter",
    "JsonlWriter",
    "MarkdownAppender",
    "append_text",
    "read_jsonl",
]


def _fsync_append(path: Path, data: bytes) -> int:
    """Append ``data`` to ``path``, flush, and fsync; return bytes written.

    Creates parent directories (never earlier) and the file itself on first
    append via binary append mode — existing content is never rewritten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return len(data)


class JsonlWriter:
    """Append-only JSONL writer (ARCHITECTURE.md section 7.1).

    Each :meth:`append` serializes one record as a single compact JSON line
    terminated by ``"\\n"``, appends it in binary append mode, flushes, and
    fsyncs before returning. A module-level ``threading.Lock`` guards the
    append+fsync critical section so multiple threads may append safely.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: Mapping) -> int:
        """Serialize ``record`` as one JSON line and durably append it.

        Returns the byte length written (including the trailing newline).
        """
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            return _fsync_append(self._path, data)


class MarkdownAppender:
    """Append-only markdown writer with the same fsync durability contract.

    Each :meth:`append` writes the raw text exactly as given (the caller owns
    separators), flushes, and fsyncs. :meth:`append_line` is a convenience
    that ensures the appended text ends with exactly one trailing newline
    (one is added only when missing).
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def append(self, text: str) -> int:
        """Durably append raw ``text``; return the bytes written."""
        data = text.encode("utf-8")
        with self._lock:
            return _fsync_append(self._path, data)

    def append_line(self, text: str) -> int:
        """Durably append ``text`` as a line: a single ``"\\n"`` is appended
        if the text does not already end with one."""
        if not text.endswith("\n"):
            text = text + "\n"
        return self.append(text)


def read_jsonl(path: str | os.PathLike) -> list[dict]:
    """Read an append-only JSONL file, tolerating a torn final line.

    Lines are parsed with :func:`json.loads` one by one. Whitespace-only
    lines are skipped. If the last content-bearing line fails to parse
    (torn final line from a partial write), it is silently ignored and the
    parsed prefix is returned. A parse failure on any non-final line is real
    corruption and :class:`json.JSONDecodeError` propagates. A parsed line
    that is not a JSON object raises :class:`ValueError` naming the path and
    line number (1-based). A missing file yields an empty list. No
    directories are created.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    lines = text.splitlines()
    last_content = -1
    for index, line in enumerate(lines):
        if line.strip():
            last_content = index
    records: list[dict] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            if index == last_content:
                break  # torn final line: the last write lost at most this line
            raise
        if not isinstance(record, dict):
            raise ValueError(
                f"{file_path}: line {index + 1}: expected a JSON object "
                f"(dict), got {type(record).__name__}"
            )
        records.append(record)
    return records


def append_text(path: str | os.PathLike, text: str) -> int:
    """Function form of :meth:`MarkdownAppender.append`: durably append raw
    ``text`` (with fsync) and return the bytes written."""
    return MarkdownAppender(path).append(text)


class AtomicCounter:
    """Atomically rewritten JSON counter file (design P1.3).

    The value is stored in ``path`` as a single-line JSON mapping. Every write
    goes through ``<path>.tmp`` + fsync + ``os.replace`` + a directory-fd
    fsync, so readers always observe either the old or the new mapping —
    never a mixed state — and the rename is durable. If a read hits a torn or
    unreadable file (e.g. a legacy crash-mid-write), the last known good
    in-memory mapping is used as fallback.
    """

    def __init__(self, path: str | os.PathLike, initial: dict | None = None) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict = dict(initial) if initial else {}
        if self._path.is_file():
            try:
                loaded = self._read_file()
            except (OSError, ValueError):
                return  # torn/unreadable file: fall back to initial
            if isinstance(loaded, dict):
                self._data = loaded

    def _read_file(self) -> object:
        # json.JSONDecodeError and UnicodeDecodeError both subclass ValueError,
        # so a torn final line surfaces as ValueError here.
        text = self._path.read_text(encoding="utf-8")
        return json.loads(text)

    def _read_state(self) -> dict:
        """Refresh in-memory state from disk, falling back to the last known
        good mapping on torn/unreadable content."""
        if self._path.is_file():
            try:
                loaded = self._read_file()
            except (OSError, ValueError):
                return self._data
            if isinstance(loaded, dict):
                self._data = loaded
        return self._data

    def _write_atomic(self, mapping: dict) -> None:
        """Write ``mapping`` to ``path`` atomically (write tmp -> fsync ->
        os.replace -> fsync parent directory fd)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        data = (
            json.dumps(mapping, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self._path)
        dir_fd = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass  # not every filesystem supports directory fsync
        finally:
            os.close(dir_fd)

    def load(self) -> dict:
        """Re-read the file if it exists (tolerating a torn final line by
        falling back to the last known good mapping) and return a copy of the
        current dict."""
        with self._lock:
            return dict(self._read_state())

    def bump(self, key: str, n: int = 1) -> dict:
        """Increment the counter ``key`` by ``n`` and rewrite the whole file
        atomically; return the updated dict."""
        with self._lock:
            current = dict(self._read_state())
            try:
                value = int(current.get(key, 0))
            except (TypeError, ValueError):
                value = 0
            current[key] = value + n
            self._data = dict(current)
            self._write_atomic(current)
            return current

    def set(self, mapping: Mapping) -> dict:
        """Merge ``mapping`` into the current state and rewrite the file
        atomically; return the updated dict."""
        with self._lock:
            current = dict(self._read_state())
            current.update(mapping)
            self._data = dict(current)
            self._write_atomic(current)
            return current

    def get(self, key: str, default=None):
        """Return the current value of ``key`` (re-reading the file)."""
        return self.load().get(key, default)

    def save(self) -> dict:
        """Write the current in-memory mapping atomically (no bump) and
        return a copy of it."""
        with self._lock:
            self._write_atomic(self._data)
            return dict(self._data)
