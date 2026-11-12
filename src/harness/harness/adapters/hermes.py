"""Hermes agent adapter (ARCHITECTURE.md section 2.2, phase R4).

Runs one one-shot hermes chat per :class:`~harness.adapters.base.RunSpec`:
``hermes [-p <profile>] chat --oneshot --query-file <tmpfile> ...``, relays the
child's stdout line-by-line as ``chunk`` events through a documented
decorative-line filter, records one JSONL ledger row per run, and maps
failures / timeout to one terminal ``failed`` event (plan F1/F2/F12).

Design contract / live facts (probes recorded 2026-11-12; see
``tests/fixtures/hermes-*.out``):

- argv order is an exact snapshot contract (task Appendix A): ``-p <profile>``
  is present IFF ``spec.profile`` is set — non-profile chat runs omit ``-p``
  ENTIRELY (both forms are snapshot-tested); ``-m <model>`` only when a model
  resolves (``spec.model`` else config ``default_model``); ``--in <workdir>``
  only when a workdir is set; the prompt travels in a
  ``tempfile.NamedTemporaryFile`` (``--query-file``) so long prompts never hit
  argv limits; ``--run-budget`` carries the resolved timeout: ``spec.timeout_s``
  overrides everything, else ``profile_timeouts[profile]``, else config
  ``agents.hermes.timeout_s``.
- ``HERMES_HOME`` child-env fact learned live: profiles resolve under
  ``$HERMES_HOME``; with ``HOME=/opt/data`` and ``HERMES_HOME`` unset the CLI
  fails with "Profile 'simple' does not exist" because ``~/.hermes`` does not
  exist in the container — profiles live at ``/opt/data/profiles``. The child
  env therefore MUST pass ``HERMES_HOME`` (from the ``hermes_home`` ctor arg,
  defaulting to ``os.environ.get("HERMES_HOME", "/opt/data")``).
- Child env allowlist (M-3): only ``PATH``/``HOME``/``TERM``/``HERMES_HOME``
  plus optional profile ``.env`` passthrough keys from the ctor; nothing
  matching ``HARNESS_*`` can ever pass (see ``base.build_child_env``).
- Spawn: ``cwd=spec.workdir`` when set (harness cwd otherwise),
  ``start_new_session=True`` (killpg watchdog), ``stdin=DEVNULL``, stdout/stderr
  PIPE; stderr is accumulated in memory and its last 500 chars are reported in
  ``failed`` events (child output is bounded by hermes' own ``--run-budget``;
  ``max_output_bytes`` is enforced supervisor-side, not here).
- Relay: every stdout line becomes a ``chunk`` event after stripping the
  trailing newline and pty ``\\r`` artifacts, EXCEPT lines matched by
  :data:`LINE_FILTERS` (query echo, banner, box/rule decoration, blanks);
  ``Resume this session with:`` help lines and ``Session:``/``Title:``/
  ``Duration:``/``Messages:`` footer lines are faithfully relayed. Hermes runs
  emit NO synthetic ``final`` event on success — the last relayed chunk is the
  stream tail; a ``failed`` event, when present, is terminal (F1/F2).
- Session id: extracted from stdout (``Session:`` line first, then the
  ``hermes --resume`` help line), falling back to a read-only sqlite peek at
  the profile store (``tests/fixtures/hermes-sqlite-store-NOTE.md``).
- ctor: either the full :class:`~harness.config.HarnessConfig` (ledger path
  defaults to ``cfg.storage.data_dir``) or just the
  :class:`~harness.config.HermesAgentsConfig` sub-config plus a required
  ``persist_dir``.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from harness.adapters import base
from harness.adapters.base import RunSpec, StreamEvent, build_child_env
from harness.config import HarnessConfig, HermesAgentsConfig
from harness.persist import JsonlWriter

__all__ = [
    "LINE_FILTERS",
    "HermesAdapter",
    "build_child_env",
    "extract_session_id",
    "newest_session_id_from_store",
]

LEDGER_FILE_NAME = "runs_ledger.jsonl"

# Decorative hermes CLI banner/sanitizer lines that are NOT relayed as chunks
# (live-probe evidence 2026-11-12: tests/fixtures/hermes-oneshot-stdout.txt).
# Allow-deny style: a relayed line matching ANY entry is suppressed; every
# other line is relayed verbatim. Each entry has a comment on what live line
# it eats.
LINE_FILTERS: tuple[re.Pattern[str], ...] = (
    # The CLI echoes the query file content back ("Query: <prompt first line>").
    re.compile(r"^Query: "),
    # Startup banner printed directly under the query echo.
    re.compile(r"^Initializing agent\.\.\.$"),
    # Horizontal rule drawn as a run of U+2500 box-drawing dashes.
    re.compile(r"^\u2500+$"),
    # Reply box borders ("╭─ ☤ Hermes …" top, "╰──…╯" bottom).
    re.compile(r"^\u256d\u2500"),
    re.compile(r"^\u2570\u2500"),
    # Empty / pty-blank lines (the CRLF reply box emits blank padding lines).
    re.compile(r"^\s*$"),
)

# Session-id extraction patterns (fixture-verified):
# 1. the footer line is `Session:<whitespace run><id>` (TAB + spaces observed
#    live), matched FIRST with flexible whitespace;
# 2. the resume help line `  hermes --resume <id> -p <profile>` is the
#    fallback for runs whose footer never got printed.
_SESSION_LINE_RE = re.compile(r"^\s*Session:\s+(\S+)", re.MULTILINE)
_RESUME_HELP_RE = re.compile(r"hermes --resume (\S+)")

_STDERR_TAIL_CHARS = 500


def extract_session_id(stdout_text: str) -> str | None:
    """Extract the hermes session id from run stdout text.

    Prefers the ``Session:`` footer line (``_SESSION_LINE_RE``), falling back
    to the ``hermes --resume <id>`` help line (``_RESUME_HELP_RE``); both are
    unit-tested against the recorded live fixtures. ``None`` when absent.
    """
    match = _SESSION_LINE_RE.search(stdout_text)
    if match is not None:
        return match.group(1)
    match = _RESUME_HELP_RE.search(stdout_text)
    if match is not None:
        return match.group(1)
    return None


def newest_session_id_from_store(profile_dir: Path) -> str | None:
    """FALLBACK newest session id: read-only peek at `<profile_dir>/state.db`.

    Only used when stdout extraction fails (e.g. the run budget killed the CLI
    before the footer block was printed, but the session was still recorded).
    Opens ``<profile_dir>/state.db`` READ-ONLY via
    ``sqlite3.connect("file:...?mode=ro", uri=True)`` and runs
    ``SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1`` on the
    ``sessions`` table (schema facts documented in
    ``tests/fixtures/hermes-sqlite-store-NOTE.md``). A missing database, an
    empty ``sessions`` table, or any sqlite/OSError yields ``None`` — the
    fallback is best-effort and never raises.
    """
    db_path = Path(profile_dir) / "state.db"
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except (OSError, sqlite3.Error):  # pragma: no cover - open is the only step here
        return None
    try:
        row = connection.execute(
            "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    finally:
        connection.close()
    return row[0] if row is not None else None


def _normalize_line(raw_line: bytes) -> str | None:
    """Decode/strip one stdout line; ``None`` when it matches :data:`LINE_FILTERS`.

    The trailing ``\\n`` and pty ``\\r`` artifacts are stripped so the fixture
    lines (CRLF from the live probe) match the filter patterns cleanly.
    """
    line = raw_line.decode("utf-8", errors="replace")
    line = line.removesuffix("\n")
    line = line.rstrip("\r")
    for pattern in LINE_FILTERS:
        if pattern.search(line):
            return None
    return line


class HermesAdapter:
    """One-shot hermes chat runner implementing :class:`~harness.adapters.base.AgentRun`."""

    def __init__(
        self,
        config: HarnessConfig | HermesAgentsConfig,
        persist_dir: str | Path | None = None,
        *,
        hermes_home: str | None = None,
        toolsets: str | None = None,
        reasoning_level: str | None = None,
        profile_env: Mapping[str, str] | None = None,
    ) -> None:
        """Take the full HarnessConfig OR the hermes sub-config + persist_dir.

        With the full :class:`~harness.config.HarnessConfig` the ledger is
        appended at ``<storage.data_dir>/runs_ledger.jsonl``; with the bare
        ``HermesAgentsConfig`` sub-config, ``persist_dir`` is required.
        ``toolsets`` / ``reasoning_level`` are optional ctor kwargs because no
        config keys exist for the hermes ``-t`` / ``--reasoning`` flags (they
        are omitted from argv when ``None``). ``profile_env`` optionally adds
        child-env passthrough keys (profile ``.env`` values; default none,
        ``HARNESS_*`` keys rejected by the env builder).
        """
        if isinstance(config, HarnessConfig):
            agents_cfg = config.agents.hermes
            persist_root = Path(
                persist_dir if persist_dir is not None else config.storage.data_dir
            )
        else:
            if persist_dir is None:
                raise ValueError(
                    "HermesAdapter requires persist_dir when given only the hermes sub-config"
                )
            agents_cfg = config
            persist_root = Path(persist_dir)
        self._cfg = agents_cfg
        self._bin = agents_cfg.bin
        self._model = agents_cfg.default_model
        self._toolsets = toolsets
        self._reasoning_level = reasoning_level
        self._profile_env = dict(profile_env) if profile_env else None
        # Container fact (module docstring): the child needs HERMES_HOME at the
        # profile store root (/opt/data in the PoC container).
        self._hermes_home = hermes_home if hermes_home is not None else os.environ.get(
            "HERMES_HOME", "/opt/data"
        )
        self._ledger = JsonlWriter(persist_root / LEDGER_FILE_NAME)

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        """Stream one hermes one-shot run: relayed chunks, terminal failed on error.

        Success ends the stream after the last relayed chunk (no synthetic
        ``final`` event — see module docstring); errors (nonzero exit incl.
        missing bin, watchdog timeout) end with exactly one ``failed`` event
        carrying the honest reason and, when known, the child exit code.
        """
        started = time.monotonic()
        timeout = self._resolve_timeout(spec)
        argv: list[str] = []
        proc: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        exit_code: int | None = None
        session: str | None = None
        stderr_text = ""
        error: str | None = None
        timed_out = False
        try:
            async with asyncio.timeout(timeout):
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", prefix="harness-query-", suffix=".txt"
                ) as query_file:
                    query_file.write(spec.prompt)
                    query_file.flush()
                    argv = list(self._build_argv(spec, query_file.name, timeout))
                    proc = await self._spawn(argv, spec.workdir)
                    stderr_task = asyncio.create_task(base.read_stream(proc.stderr))
                    stdout_parts: list[str] = []
                    try:
                        async for raw_line in proc.stdout:
                            line = _normalize_line(raw_line)
                            if line is None:
                                continue
                            stdout_parts.append(line)
                            yield StreamEvent(kind="chunk", text=line)
                        exit_code = await proc.wait()
                    finally:
                        # Watchdog / abandoned-consumer cleanup: the child tree
                        # must die with the run stream (killpg, plan F2).
                        if exit_code is None and proc.returncode is None:
                            await base.kill_process_group(proc, grace_s=1.0)
                            exit_code = proc.returncode
                    stderr_text = (await stderr_task).decode("utf-8", errors="replace")
                    session = extract_session_id("\n".join(stdout_parts))
                    if session is None:
                        profile_dir = self._profile_dir(spec.profile)
                        if profile_dir is not None:
                            session = newest_session_id_from_store(profile_dir)
                    if exit_code != 0:
                        tail = stderr_text[-_STDERR_TAIL_CHARS:]
                        error = f"hermes exited with code {exit_code}: {tail}"
        except GeneratorExit:
            # Consumer abandoned the stream mid-chunk: the finally blocks above
            # already killed the child and wrote the ledger; re-raise so the
            # trailing failed-event yield below is skipped (no yields during
            # generator close).
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            raise
        except TimeoutError:
            timed_out = True
            error = f"timeout after {timeout}s"
            if proc is not None:
                await base.kill_process_group(proc)
                exit_code = proc.returncode
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()  # absorb the cancel: buffer read stays clean
        except OSError as exc:
            # F12: config-pinned bin missing/garbage -> honest spawn failure.
            error = f"failed to spawn hermes {self._bin!r}: {exc}"
        finally:
            row: dict[str, object] = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "agent": "hermes",
                "argv_redacted": list(argv),
                "exit_code": exit_code,
                "session_ref": session,
                "duration_s": round(time.monotonic() - started, 3),
            }
            if error is not None:
                row["error"] = error
            self._ledger.append(row)
        if error is not None:
            # Timeout/spawn failures carry no exit code (the child never
            # exited normally); nonzero exits carry their code (F1).
            if timed_out or exit_code is None:
                yield StreamEvent(kind="failed", error=error)
            else:
                yield StreamEvent(kind="failed", error=error, exit_code=exit_code)

    def _resolve_timeout(self, spec: RunSpec) -> int:
        """``spec.timeout_s`` overrides both; else ``profile_timeouts[profile]``;
        else the config ``timeout_s``."""
        if spec.timeout_s is not None:
            return spec.timeout_s
        if spec.profile is not None and spec.profile in self._cfg.profile_timeouts:
            return self._cfg.profile_timeouts[spec.profile]
        return self._cfg.timeout_s

    def _build_argv(self, spec: RunSpec, query_file_path: str, timeout: int) -> list[str]:
        """Exact snapshot argv (task Appendix A; order is part of the contract)."""
        argv = [self._bin]
        if spec.profile is not None:  # -p present IFF profile set (non-profile omits it)
            argv += ["-p", spec.profile]
        argv += ["chat", "--oneshot", "--query-file", str(query_file_path)]
        model = spec.model if spec.model is not None else self._model
        if model:
            argv += ["-m", str(model)]
        if self._toolsets:
            argv += ["-t", str(self._toolsets)]
        if self._reasoning_level:
            argv += ["--reasoning", str(self._reasoning_level)]
        if spec.resume_ref:
            argv += ["--resume", spec.resume_ref]
        if spec.workdir:
            argv += ["--in", spec.workdir]
        argv += ["--no-restore-cwd", "--source", "harness", "--run-budget", str(timeout)]
        return argv

    def _profile_dir(self, profile: str | None) -> Path | None:
        """``$HERMES_HOME/profiles/<profile>`` — the state.db store root."""
        if not profile:
            return None
        return Path(self._hermes_home) / "profiles" / profile

    async def _spawn(
        self,
        argv: list[str],
        workdir: str | None,
    ) -> asyncio.subprocess.Process:
        """Spawn the child (cwd=workdir or harness cwd) with the allowlisted env.

        Test seam: monkeypatching this method captures the assembled argv/env
        without spawning (argv snapshot matrix, M-3 env assertions, F1 stream
        shaping); the real path exercises :func:`base.start_process`.
        """
        env = build_child_env(
            dict(os.environ),
            {"HERMES_HOME": self._hermes_home, **(self._profile_env or {})},
            keep=("PATH", "HOME", "TERM", "HERMES_HOME"),
        )
        cwd = workdir if workdir else os.getcwd()
        proc, _created_group = await base.start_process(argv, cwd, env)
        return proc
