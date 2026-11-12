"""OpenCode agent adapter (ARCHITECTURE.md section 2.2, phase R4).

Runs one `opencode run <prompt> --format json ...` per
:class:`~harness.adapters.base.RunSpec` inside a validated workdir, parses the
child's NDJSON event stream into :class:`~harness.adapters.base.StreamEvent` s,
records one JSONL ledger row per run, and maps failures/timeout to a terminal
``failed`` event (plan F1/F2/F3/F12).

Design contract / live facts (probes recorded 2026-11-12, see
``tests/fixtures/opencode-*.out`` and ``opencode-events.jsonl``):

- argv order is an exact snapshot contract: ``[bin, 'run', <prompt-as-one-argv-
  element>, '--format', 'json', ('--session', <id> | '--continue'), ('--model',
  <model>)?, ('--agent', <agent>)?, '--dir', <workdir>, '--print-logs',
  '--log-level', 'WARN', '--auto'?]``. ``--session`` only with ``resume_ref``;
  ``--continue`` only when ``resume_ref`` is unset and
  ``spec.resume_requested``; ``--model`` from ``spec.model`` else config
  ``default_model`` (omitted when neither); ``--agent`` is included ALWAYS for
  run specs (design 2.2; ``spec.agent`` overrides config ``default_agent``);
  ``--auto`` included when ``agents.opencode.auto_approve`` is true (verified
  present in opencode 1.18.32: "auto-approve permissions that are not
  explicitly denied (dangerous!)").
- workdir validation (m3): the workdir MUST live under the configured
  ``workdir_root`` — regex ``re.escape(workdir_root) + "/"``, fullmatch — and
  is mandatory because cwd AND ``--dir`` are both set to it. Everything else
  raises ``ValueError`` before any spawn.
- opencode.json pre-seed (F11): when `<workdir>/opencode.json` is missing it is
  created with ``{"permission": {"edit": "allow", "bash": "allow", "webfetch":
  "allow"}, "external_directory": "allow"}``; an existing file is never
  touched.
- NDJSON stdout contract: every line is ``json.loads``-ed; unparseable lines
  are relayed as ``system(raw=line)`` WITHOUT crashing (F3 — garbage relayed,
  stream continues). ``type=text`` events carry the FULL incremental text part
  and become ``chunk(text)`` events; ``step_start``/``step_finish`` are skipped
  as noise (documented choice); ``tool_use`` parts (``part.type == 'tool'``)
  become ``tool`` events with the state title or an ≤80-char input preview;
  unknown ``type`` values are relayed as ``system(raw=<line>)``; a
  ``type=error`` event becomes a terminal ``failed`` event (error text from
  ``error.data.message`` when present) and stops the stream immediately.
- final text: one ``final`` event is emitted at stream end (normal EOF path,
  before the exit-code check) with ``final_text`` = ALL text-part strings
  joined (they are full text parts; verified live: the final text of a
  step_start..step_finish block equals the emitted text part, e.g.
  "PROBE_OK") and ``session_ref`` = the ``sessionID`` of the last event with
  one (top-level camelCase ``sessionID`` on every event line). The error/stop
  path skips the ``final`` event (failure supersedes it).
- exit handling: opencode error runs exit 1 (fixture opencode-failure-probe.*)
  — BOTH an ``error`` event and a nonzero exit map to ``failed``; only the
  first failure is emitted (one terminal failed event max per run).
- Child env: same strict allowlist helper as hermes (:func:`base.build_child_env`)
  but with ``OPENCODE_*`` keys in the keep-prefixes (config passthrough);
  ``HARNESS_*`` (incl. ``HARNESS_API_KEY`` / ``HARNESS_ROUTER_*``) can never
  pass (M-3). Spawn: ``cwd=workdir``, ``start_new_session=True``,
  ``stdin=DEVNULL``, stderr PIPE (accumulated; tail reported on failures).
- ctor takes the full :class:`~harness.config.HarnessConfig` or the
  :class:`~harness.config.OpencodeAgentsConfig` sub-config plus a required
  ``persist_dir`` (ledger at ``<persist_dir>/runs_ledger.jsonl``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from harness.adapters import base
from harness.adapters.base import RunSpec, StreamEvent, build_child_env, read_stream
from harness.config import HarnessConfig, OpencodeAgentsConfig
from harness.persist import JsonlWriter

__all__ = ["OpencodeAdapter", "extract_session_id", "preseed_opencode_json"]

LEDGER_FILE_NAME = "runs_ledger.jsonl"

_STDERR_TAIL_CHARS = 500

_TOOL_BRIEF_MAX_CHARS = 80

_PERMISSION_SEED: dict[str, object] = {
    "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
    "external_directory": "allow",
}


def _workdir_regex(workdir_root: str) -> re.Pattern[str]:
    """``^<escaped workdir_root>/<nesting>`` — nested paths under the root.

    The pattern is ``re.escape(workdir_root) + "/"`` used with an anchored
    ``.match`` (start-anchored only, so nested paths under the root pass).
    """
    return re.compile(re.escape(workdir_root) + "/")


def preseed_opencode_json(workdir: str | Path) -> bool:
    """F11: create ``<workdir>/opencode.json`` when missing; True if created.

    Writes the permission seed documented in the module docstring. An existing
    ``opencode.json`` is never overwritten (returns False).
    """
    seed_path = Path(workdir) / "opencode.json"
    if seed_path.exists():
        return False
    seed_path.write_text(
        json.dumps(_PERMISSION_SEED, indent=2) + "\n", encoding="utf-8"
    )
    return True


def extract_session_id(events_text_or_last_json: str | dict) -> str | None:
    """Extract the opencode ``sessionID`` (camelCase, top-level of events).

    Accepts a single event dict (``obj.get('sessionID')``, fixture-verified
    field name) or a text blob of NDJSON event lines (the last event line with
    a ``sessionID`` wins). Unparseable lines are ignored; ``None`` when no
    event carries a session id.
    """
    if isinstance(events_text_or_last_json, dict):
        value = events_text_or_last_json.get("sessionID")
        return str(value) if value else None
    last: str | None = None
    for line in events_text_or_last_json.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("sessionID"):
            last = str(obj["sessionID"])
    return last


def _tool_brief(part: dict) -> str | None:
    """State title, else a ≤80-char JSON preview of the tool input."""
    state = part.get("state")
    if not isinstance(state, dict):
        return None
    title = state.get("title")
    if title:
        return str(title)
    tool_input = state.get("input")
    if tool_input is None:
        return None
    preview = (
        json.dumps(tool_input, ensure_ascii=False)
        if isinstance(tool_input, dict)
        else str(tool_input)
    )
    return preview[:_TOOL_BRIEF_MAX_CHARS]


class OpencodeAdapter:
    """``opencode run`` NDJSON runner implementing :class:`~harness.adapters.base.AgentRun`."""

    def __init__(
        self,
        config: HarnessConfig | OpencodeAgentsConfig,
        persist_dir: str | Path | None = None,
    ) -> None:
        """Take the full HarnessConfig OR the opencode sub-config + persist_dir.

        With the full :class:`~harness.config.HarnessConfig` the ledger is
        appended at ``<storage.data_dir>/runs_ledger.jsonl``; with the bare
        ``OpencodeAgentsConfig`` sub-config, ``persist_dir`` is required.
        """
        if isinstance(config, HarnessConfig):
            agents_cfg = config.agents.opencode
            persist_root = Path(
                persist_dir if persist_dir is not None else config.storage.data_dir
            )
        else:
            if persist_dir is None:
                raise ValueError(
                    "OpencodeAdapter requires persist_dir when given only the opencode sub-config"
                )
            agents_cfg = config
            persist_root = Path(persist_dir)
        self._cfg = agents_cfg
        self._bin = agents_cfg.bin
        self._model = agents_cfg.default_model
        self._agent = agents_cfg.default_agent
        self._auto_approve = agents_cfg.auto_approve
        self._workdir_root = agents_cfg.workdir_root
        self._workdir_re = _workdir_regex(agents_cfg.workdir_root)
        self._ledger = JsonlWriter(persist_root / LEDGER_FILE_NAME)

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        """Stream one opencode run: NDJSON-parsed chunks/tools, final, failed-on-error.

        Validation errors (workdir) raise ``ValueError`` on first iteration
        before any spawn; terminal states are exactly one ``failed`` event
        (error event, nonzero exit, watchdog timeout, missing bin) — the error
        path stops immediately and skips the ``final`` event.
        """
        started = time.monotonic()
        timeout = self._resolve_timeout(spec)
        workdir = self._validate_workdir(spec)
        argv = self._build_argv(spec, workdir)
        proc: asyncio.subprocess.Process | None = None
        exit_code: int | None = None
        last_session: str | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        error: str | None = None
        error_is_exit: bool = False
        chunks: list[str] = []
        try:
            async with asyncio.timeout(timeout):
                Path(workdir).mkdir(parents=True, exist_ok=True)
                preseed_opencode_json(workdir)
                env = build_child_env(
                    dict(os.environ), keep_prefixes=("OPENCODE_",)
                )
                proc = await self._spawn(argv, workdir, env)
                stderr_task = asyncio.create_task(read_stream(proc.stderr))
                failure_event: StreamEvent | None = None
                try:
                    async for raw_line in proc.stdout:
                        line = raw_line.decode("utf-8", errors="replace")
                        line = line.removesuffix("\n")
                        line = line.rstrip("\r")
                        if not line.strip():
                            continue  # blank NDJSON padding — nothing to relay
                        try:
                            event_obj = json.loads(line)
                        except ValueError:
                            # F3: garbage line relayed as system raw, no crash.
                            yield StreamEvent(kind="system", raw=line)
                            continue
                        if not isinstance(event_obj, dict):
                            yield StreamEvent(kind="system", raw=line)
                            continue
                        kind = event_obj.get("type")
                        part = event_obj.get("part")
                        part_dict: dict = part if isinstance(part, dict) else {}
                        if kind == "step_start" or kind == "step_finish":
                            continue  # noise (documented choice)
                        if kind == "text":
                            text = str(part_dict.get("text", ""))
                            chunks.append(text)
                            yield StreamEvent(kind="chunk", text=text)
                        elif kind == "tool_use" and part_dict.get("type") == "tool":
                            yield StreamEvent(
                                kind="tool",
                                tool_name=str(part_dict.get("tool", "")),
                                tool_brief=_tool_brief(part_dict),
                            )
                        elif kind == "error":
                            # Error event -> terminal failed, then stop (the
                            # final event is skipped: failure supersedes it).
                            failure_event = StreamEvent(
                                kind="failed", error=_error_text(event_obj)
                            )
                        else:
                            # Unknown type: relay the full raw line (F3 family).
                            yield StreamEvent(kind="system", raw=line)
                        event_session = event_obj.get("sessionID")
                        if event_session:
                            last_session = str(event_session)
                        if failure_event is not None:
                            break
                    try:
                        # EOF normally exits within an instant; a mid-stream
                        # stop (error event) can leave the child blocked on an
                        # unread pipe — cap the wait, then killpg.
                        exit_code = await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except TimeoutError:
                        await base.kill_process_group(proc, grace_s=1.0)
                        exit_code = proc.returncode
                finally:
                    # Abandoned-consumer cleanup: kill the child tree (F2).
                    if exit_code is None and proc.returncode is None:
                        await base.kill_process_group(proc, grace_s=1.0)
                        exit_code = proc.returncode
                stderr_text = (
                    (await stderr_task).decode("utf-8", errors="replace")
                    if stderr_task is not None
                    else ""
                )
                tail = stderr_text[-_STDERR_TAIL_CHARS:]
                # final event at stream end, BEFORE the exit-code check; the
                # error/stop path never reaches here (failure supersedes).
                if failure_event is None:
                    yield StreamEvent(
                        kind="final",
                        final_text="".join(chunks),
                        session_ref=last_session,
                    )
                    if exit_code != 0:
                        failure_event = StreamEvent(
                            kind="failed",
                            error=f"opencode exited with code {exit_code}: {tail}",
                            exit_code=exit_code,
                        )
                        error_is_exit = True
                if failure_event is not None:
                    error = failure_event.error
        except GeneratorExit:
            # Consumer abandoned the stream: cleanup already ran; the ledger
            # write below happens on the unwind and yields are forbidden.
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
            raise
        except TimeoutError:
            if proc is not None:
                await base.kill_process_group(proc)
                exit_code = proc.returncode
            error = f"timeout after {timeout}s"
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()  # absorb the cancel: no pending-reader leak
        except OSError as exc:
            # F12: config-pinned bin missing/garbage -> honest spawn failure.
            error = f"failed to spawn opencode {self._bin!r}: {exc}"
        finally:
            row: dict[str, object] = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "agent": "opencode",
                "argv_redacted": list(argv),
                "exit_code": exit_code,
                "session_ref": last_session,
                "duration_s": round(time.monotonic() - started, 3),
            }
            if error is not None:
                row["error"] = error
            self._ledger.append(row)
        if error is not None:
            # Only the exit-code failure path (F1) carries the exit code;
            # timeout / spawn / error-event failures have no normal exit.
            exit_code_for_failure = exit_code if error_is_exit else None
            yield StreamEvent(
                kind="failed", error=error, exit_code=exit_code_for_failure
            )

    def _resolve_timeout(self, spec: RunSpec) -> int:
        """``spec.timeout_s`` overrides; else the config ``timeout_s``."""
        return spec.timeout_s if spec.timeout_s is not None else self._cfg.timeout_s

    def _validate_workdir(self, spec: RunSpec) -> str:
        """m3: workdir required and must be under the configured root."""
        workdir = spec.workdir
        if not workdir:
            raise ValueError(
                "opencode run requires a workdir (cwd AND --dir are set to it)"
            )
        if self._workdir_re.match(workdir) is None:
            raise ValueError(
                f"workdir {workdir!r} is not under opencode workdir_root {self._workdir_root!r}"
            )
        return workdir

    def _build_argv(self, spec: RunSpec, workdir: str) -> list[str]:
        """Exact snapshot argv (task contract; order is part of the contract)."""
        argv = [self._bin, "run", spec.prompt, "--format", "json"]
        if spec.resume_ref:
            argv += ["--session", spec.resume_ref]
        elif spec.resume_requested:
            argv += ["--continue"]
        model = spec.model if spec.model is not None else self._model
        if model:
            argv += ["--model", str(model)]
        agent = spec.agent if spec.agent is not None else self._agent
        argv += ["--agent", str(agent)]
        argv += ["--dir", workdir, "--print-logs", "--log-level", "WARN"]
        if self._auto_approve:
            argv += ["--auto"]
        return argv

    async def _spawn(
        self,
        argv: list[str],
        workdir: str,
        env: Mapping[str, str],
    ) -> asyncio.subprocess.Process:
        """Spawn cwd=workdir (m3: cwd AND --dir), allowlist-filtered env.

        Test seam: monkeypatching this method captures argv/env/workdir
        without spawning (snapshot matrix, F11, F1 stream shaping); the real
        path exercises :func:`base.start_process`.
        """
        proc, _created_group = await base.start_process(argv, workdir, env)
        return proc


def _error_text(event_obj: Mapping[str, object]) -> str:
    """Human error text for a ``type=error`` NDJSON event.

    Prefers ``error.data.message`` (failure-probe shape:
    ``{"type":"error","error":{"name":...,"data":{"message":...}}}``), falling
    back to any ``error.message``, then the stringified error mapping.
    """
    error_field = event_obj.get("error")
    if isinstance(error_field, Mapping):
        data = error_field.get("data")
        if isinstance(data, Mapping) and data.get("message"):
            return str(data["message"])
        if error_field.get("message"):
            return str(error_field["message"])
    return "" if error_field is None else str(error_field)
