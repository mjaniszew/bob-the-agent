"""Adapter test doubles and helpers (explicitly imported; NOT an auto-conftest).

Provides fake subprocess-like objects for adapter run-stream tests (no real
spawns) plus a :func:`install_fake_spawn` monkeypatch helper that intercepts
``harness.adapters.base.start_process`` so the adapters' REAL ``_spawn`` paths
(build env allowlist, cwd) run while the child creation is captured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeStream:
    """Minimal async-iterable byte stream mimicking a subprocess pipe."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def read(self) -> bytes:
        """Drain to EOF (matches asyncio StreamReader semantics for tasks)."""
        data = b"".join(self._chunks)
        self._chunks = []
        return data


class FakeProc:
    """Stand-in for asyncio.subprocess.Process fed from scripted lines."""

    def __init__(
        self,
        stdout_chunks: list[bytes],
        stderr_chunks: bytes | list[bytes] = b"",
        exit_code: int = 0,
    ) -> None:
        self.stdout = FakeStream(stdout_chunks)
        stderr_list = []
        if isinstance(stderr_chunks, list):
            stderr_list = list(stderr_chunks)
        elif stderr_chunks:
            stderr_list = [stderr_chunks]
        self.stderr = FakeStream(stderr_list)
        self.returncode: int | None = None
        self._exit_code = exit_code
        self.pid = 424242

    async def wait(self) -> int:
        self.returncode = self._exit_code
        return self.returncode


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def fixture_lines(name: str) -> list[bytes]:
    """Fixture bytes split into keepends lines (CRLF artifacts preserved)."""
    return fixture_bytes(name).splitlines(keepends=True)


async def collect(stream: Any) -> list[Any]:
    """Collect an adapter's async-generator stream into a plain list."""
    return [item async for item in stream]


def install_fake_start_process(
    monkeypatch: pytest.MonkeyPatch,
    proc: FakeProc,
) -> list[dict[str, Any]]:
    """Patch ``base.start_process`` to return ``proc`` and record every call.

    Returns the list of ``{"argv", "cwd", "env"}`` capture dicts. The adapters
    resolve ``base.start_process`` on the module attribute at call time, so the
    patch intercepts the real ``_spawn`` path (env/cwd assembly still runs).
    """
    from harness.adapters import base as base_module

    calls: list[dict[str, Any]] = []

    async def fake_start_process(argv: list[str], cwd: Any, env: Any) -> tuple[Any, bool]:
        calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return proc, True

    monkeypatch.setattr(base_module, "start_process", fake_start_process)
    return calls


def qf_masked(argv: list[str]) -> list[str]:
    """Replace the ``--query-file`` value (temp path varies) with ``<QF>``."""
    masked = list(argv)
    for index, part in enumerate(masked):
        if part == "--query-file" and index + 1 < len(masked):
            masked[index + 1] = "<QF>"
    return masked
