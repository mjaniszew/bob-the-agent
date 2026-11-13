"""R11 in-loop scheduler (plan row-20 / ARCHITECTURE.md section 4 ``scheduler`` key).

One :class:`SchedulerEngine` owns an APScheduler :class:`AsyncIOScheduler` on
the running asyncio loop and dispatches three job kinds:

- ``spawn_agent`` — start an agent run on a fresh session (the chat-path
  equivalent; fire-and-forget relay task, ledger row at handoff),
- ``run_script`` — run an argv list (NO shell) under the child-process
  primitives of :mod:`harness.adapters.base`, recording only byte counts,
- ``drop_task`` — drop one inbox task through :class:`harness.tasks.TaskStore`
  with ``origin="scheduler"``.

The engine is dependency-light: it never imports ``harness.supervisor``,
``harness.taskloop`` or ``harness.gateway``. Job specs are validated strictly
by :func:`validate_job_spec` (same closed-registry philosophy as
``validate_subtask_drop`` in ``harness.tasks`` — unknown keys rejected by
name). Every fired run appends one row to the runs ledger
``<data_dir>/state/scheduler_runs_ledger.jsonl`` (append-only via
:class:`harness.persist.JsonlWriter`; append failures are logged, never
raised) with ``outcome`` one of ``fired``, ``error``, ``misfire_skip``
(APScheduler ``max_instances`` skips are internal: logged warning by
APScheduler itself, no ledger row — WP5 asserts the fired count directly);
every appended row is also offered to the
constructor-injected async ``sink(job_id, job_kind, outcome, payload)`` seam
(WP4 wires the real demand sink; a sink crash must not fail the job) and each
handler publishes exactly one ``schedule.fired`` bus event.

Job persistence (``scheduler.json``, ``cfg.scheduler.jobs_file`` resolved
against ``storage.data_dir`` when relative) is an atomically rewritten
``{"version": 1, "jobs": [...]}`` mapping — each job dict plus an added
``created_ts`` — through the same tmp + fsync + ``os.replace`` + directory
fsync pattern as :class:`harness.persist.AtomicCounter`. Boot restore policy
(:meth:`SchedulerEngine.restore`, supervisor calls it before ``start()``):
missing/corrupt file => empty job set + one warning (never blocks boot);
``at`` jobs whose run_date predates ``boot_now - misfire_grace_s`` are NOT
re-registered (one ``misfire_skip`` ledger row, dropped from the persisted
set); ``at`` within grace, ``cron`` and ``interval`` jobs are re-registered.

Fake-clock testability (docs only — no behavioral code): the ENGINE itself
always uses ``datetime.now(UTC)``; tests inject a ``clock=`` object with
``now(tz=None)`` / ``advance(seconds)`` for ledger timestamps and restore
decisions, and drive APScheduler deterministically by patching the
``datetime`` imported inside ``apscheduler.schedulers.base`` and
``apscheduler.triggers.{interval,cron,date}`` with a ``FakeDT`` subclass whose
``now(tz)`` returns the controllable clock (verified recipe: see the R11
scratch probes). A past-within-grace ``DateTrigger`` job fires immediately
once at ``start()`` (coalesce), which the WP1 tests use for deterministic
fires without sleeps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from harness.adapters import get as registry_get
from harness.adapters.base import RunSpec, build_child_env, kill_process_group, start_process
from harness.bus import Bus
from harness.errors import SessionFSMError
from harness.persist import JsonlWriter
from harness.tasks import DEFAULT_MAX_DESC_CHARS, TaskStore

__all__ = ["SCHEDULER_LEDGER_FILENAME", "SchedulerEngine", "SchedulerError", "validate_job_spec"]

_LOG = logging.getLogger("harness.scheduler")

JOB_KINDS: tuple[str, ...] = ("spawn_agent", "run_script", "drop_task")

# Allowed top-level job-spec keys: the union of the three kinds' payloads plus
# the common id/kind/schedule pair (strict, like validate_subtask_drop).
ALLOWED_JOB_KEYS: frozenset[str] = frozenset(
    {"id", "kind", "schedule", "prompt", "agent", "channel", "argv", "timeout_s", "description"}
)

# @-shorthand cron strings APScheduler does NOT support natively: expanded here
# into the 6-field (second minute hour day month day_of_week) form.
CRON_SHORTHANDS: dict[str, str] = {
    "@hourly": "0 0 * * * *",
    "@daily": "0 0 0 * * *",
    "@weekly": "0 0 0 * * 0",
    "@monthly": "0 0 0 1 * *",
    "@yearly": "0 0 0 1 1 *",
    "@annually": "0 0 0 1 1 *",
    "@midnight": "0 0 0 * * *",
}

# Relative `at` delay: +<int>[.<frac>]s == now + N seconds, resolved at engine
# start / trigger build (NOT at validation time).
_AT_RELATIVE_PATTERN = re.compile(r"\+[0-9]+(\.[0-9]+)?s")

SCHEDULER_LEDGER_FILENAME = Path("state") / "scheduler_runs_ledger.jsonl"


def _now_utc(clock: Any | None = None) -> datetime:
    """Aware-UTC now: the injected fake clock when given, else the real clock."""
    if clock is not None:
        return clock.now(UTC)
    return datetime.now(UTC)


def _split_cron(expr: str) -> tuple[str, ...]:
    """Expand @-shorthands, split on whitespace, EXACTLY 6 fields or fail.

    Shorthand strings (`@daily`, `@hourly`, ...) are accepted INSTEAD of the
    6-field form and mapped to their 6-field equivalent before the positional
    split. Five-field crontab input is an ERROR: callers must provide the
    seconds field explicitly.
    """
    expanded = CRON_SHORTHANDS.get(expr, expr)
    fields = tuple(expanded.split())
    if len(fields) != 6:
        raise SchedulerError(
            f"cron expr must have 6 fields (second minute hour day month day_of_week), "
            f"got {len(fields)}"
        )
    return fields


def _require_aware(value: object, parsed: datetime) -> datetime:
    """Reject naive datetimes for ``at.iso`` (SchedulerError, honest message)."""
    if parsed.tzinfo is None:
        raise SchedulerError(f"at.iso must be timezone-aware ISO datetime, got {value!r}")
    return parsed


def _resolve_at_run_date(schedule: dict, base_now: datetime) -> datetime:
    """Resolve an ``at`` schedule to a timezone-aware UTC run datetime.

    Accepts (1) the relative form ``+<int|float>s`` -> ``base_now + Ns``,
    (2) an absolute ISO string parsed with ``datetime.fromisoformat`` (naive
    forms rejected) and (3) an integer/float epoch treated as UTC epoch.
    Unknown shapes raise :class:`SchedulerError`.
    """
    value = schedule.get("iso")
    if isinstance(value, str):
        if _AT_RELATIVE_PATTERN.fullmatch(value):
            return base_now + timedelta(seconds=float(value[1:-1]))
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise SchedulerError(f"at.iso must be timezone-aware ISO datetime, got {value!r}") from exc
        return _require_aware(value, parsed)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    raise SchedulerError(f"at.iso must be timezone-aware ISO datetime, got {value!r}")


class SchedulerError(Exception):
    """Raised for invalid job specs (validation) and engine start failures."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def validate_job_spec(spec: dict, *, max_desc_chars: int = DEFAULT_MAX_DESC_CHARS) -> dict:
    """Validate one raw job dict from ``scheduler.jobs`` config; return a copy.

    The ORIGINAL dict is returned (an unnormalized copy), so callers keep
    their input; ``id`` is never mutated. Field rules:

    - ``id``: required non-empty string (kept verbatim),
    - ``kind``: one of ``spawn_agent`` / ``run_script`` / ``drop_task``,
    - ``schedule``: ``{"type": cron|interval|at, ...}`` — cron ``expr`` is the
      6-field form or an ``@``-shorthand (5-field crontab input is an ERROR),
      interval ``seconds`` must be a number > 0, ``at`` accepts a
      timezone-aware ISO string (naive rejected), a relative ``+Ns`` delay
      (resolved at engine start, not here) or an integer/float UTC epoch,
    - kind payloads: ``spawn_agent`` accepts optional ``prompt`` / ``agent`` /
      ``channel`` strings; ``run_script`` requires a non-empty list-of-strings
      ``argv`` (shell strings are forbidden by design) and an optional
      ``timeout_s`` number > 0; ``drop_task`` requires a ``description``,
      non-empty after strip and at most ``max_desc_chars`` chars.

    Unknown top-level keys are rejected by name (closed registry, the
    ``validate_subtask_drop`` style). Every failure raises
    :class:`SchedulerError` with a readable message naming the problem.
    """
    if not isinstance(spec, dict):
        raise SchedulerError(f"job spec must be a dict, got {type(spec).__name__}")
    for key in spec:
        if key not in ALLOWED_JOB_KEYS:
            raise SchedulerError(
                f"unknown job key {key} (allowed: "
                "id, kind, schedule, prompt, agent, channel, argv, timeout_s, description)"
            )
    job_id = spec.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise SchedulerError("job.id must be a non-empty string")
    kind = spec.get("kind")
    if kind not in JOB_KINDS:
        raise SchedulerError(
            f"unknown job kind {kind} (expected spawn_agent, run_script, drop_task)"
        )
    schedule = spec.get("schedule")
    if not isinstance(schedule, dict):
        raise SchedulerError("job.schedule must be a dict")
    stype = schedule.get("type")
    if stype == "cron":
        expr = schedule.get("expr")
        if not isinstance(expr, str):
            raise SchedulerError("schedule.expr is required for cron jobs and must be a string")
        _split_cron(expr)
    elif stype == "interval":
        seconds = schedule.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            raise SchedulerError("interval.seconds must be > 0")
    elif stype == "at":
        _resolve_at_run_date(schedule, datetime.now(UTC))
    else:
        raise SchedulerError(f"unknown schedule type {stype} (expected cron, interval, at)")

    if kind == "spawn_agent":
        for key in ("prompt", "agent", "channel"):
            if key in spec and not isinstance(spec[key], str):
                raise SchedulerError(f"job.{key} must be a string, got {type(spec[key]).__name__}")
    elif kind == "run_script":
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise SchedulerError("run_script.argv must be a non-empty list of strings")
        timeout_s = spec.get("timeout_s")
        if timeout_s is not None and (
            isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0
        ):
            raise SchedulerError("run_script.timeout_s must be a number > 0")
    else:  # drop_task
        description = spec.get("description")
        if not isinstance(description, str):
            raise SchedulerError(
                f"drop_task.description must be a string, got {type(description).__name__}"
            )
        if not description.strip():
            raise SchedulerError("drop_task.description must be non-empty")
        if len(description) > max_desc_chars:
            raise SchedulerError(
                f"drop_task.description is {len(description)} chars (max {max_desc_chars})"
            )
    return dict(spec)


def _atomic_write_json(path: Path, obj: dict) -> None:
    """Atomically write ``obj`` as JSON at ``path`` (persist.py shape): write
    ``<path>.tmp``, fsync, ``os.replace``, fsync the parent directory fd."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
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


class _JobRunner:
    """Module-level wrapper registered with APScheduler for ONE job.

    Holds a strong reference to the engine and the job record; ``run`` is the
    only callable ever added to the scheduler (a plain coroutine function the
    AsyncIOExecutor schedules as a task on the engine's loop).
    """

    __slots__ = ("_engine", "_record")

    def __init__(self, engine: SchedulerEngine, record: dict) -> None:
        self._engine = engine
        self._record = record

    async def run(self) -> None:
        await self._engine._run_job(self._record)


class SchedulerEngine:
    """In-loop APScheduler engine for the three R11 job kinds.

    The engine binds the running asyncio loop at :meth:`start` (the
    ``AsyncIOScheduler`` binds it inside its own ``.start()``), registers one
    :class:`_JobRunner` per job with ``misfire_grace_time`` / ``coalesce`` /
    ``max_instances=1`` / ``replace_existing=True``, and persists the merged
    job set (seed ``cfg.scheduler.jobs`` + restored ``scheduler.json``, seed
    wins on id conflicts) to ``scheduler.json``. The runs ledger writer and
    the :class:`~harness.tasks.TaskStore` are constructed lazily on first use.
    """

    def __init__(
        self,
        cfg,  # HarnessConfig
        bus: Bus | None,  # Bus or None
        manager=None,  # SessionManager (spawn_agent needs it; may be None in WP1 tests)
        *,
        jobs_file: str | None = None,  # None -> cfg.scheduler.jobs_file, data_dir-relative
        clock=None,  # FakeClock-like (now(tz=None), advance(seconds)); None -> real time
        router=None,  # Router for the default spawn_agent path (route_async)
        adapters=None,  # mapping/bare-factory adapter override (app.py _resolve_adapter shape)
        sink=None,  # async callable(row_dict) demand seam; WP1 stores + calls, WP4 wires real
        spawn=None,  # async callable(job_record, payload); None -> default spawn handler
        script=None,  # async callable(job_record, payload); None -> default run_script handler
        drop=None,  # async callable(job_record, payload); None -> default drop_task handler
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._manager = manager
        self._jobs_file_arg = jobs_file
        self._clock = clock
        self._router = router
        self._adapters = adapters
        self._sink = sink
        self._spawn = spawn
        self._script = script
        self._drop = drop
        self._grace = int(cfg.scheduler.misfire_grace_s)
        self._coalesce = bool(cfg.scheduler.coalesce)
        self._data_dir = Path(cfg.storage.data_dir)
        self._jobs: dict[str, dict] = {}  # merged in-memory registry, keyed by job id
        self._sched: AsyncIOScheduler | None = None
        self._task_store: TaskStore | None = None  # lazy: first drop_task fire
        self._ledger: JsonlWriter | None = None  # lazy: first append (no fired jobs => no file)
        self._ledger_path = self._data_dir / SCHEDULER_LEDGER_FILENAME
        self._tasks: set[asyncio.Task] = set()  # fire-and-forget relay runs (spawn default path)

    # ------------------------------------------------------------------
    # Paths + clock
    # ------------------------------------------------------------------
    @property
    def jobs_path(self) -> Path:
        """Absolute ``scheduler.json`` path (override resolved against data_dir)."""
        raw = Path(self._jobs_file_arg if self._jobs_file_arg is not None
                   else self._cfg.scheduler.jobs_file)
        if not raw.is_absolute():
            raw = self._data_dir / raw
        return raw

    def _now_utc(self) -> datetime:
        """Engine-clock now (aware UTC): injected clock or the real one."""
        return _now_utc(self._clock)

    def _now_iso(self) -> str:
        """Engine-clock UTC isoformat (ledger rows, created_ts)."""
        return self._now_utc().isoformat()

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------
    def _build_trigger(self, schedule: dict, record: dict | None = None) -> object:
        """APScheduler trigger for one validated schedule dict (UTC-hardcoded:
        HarnessConfig carries no timezone key, per plan decision).

        Interval jobs re-anchor at the persisted ``created_ts`` (falling back
        to restore-now when it is absent or unparsable) so a restored job
        keeps the original boundary series instead of drifting by the boot
        delay.
        """
        stype = schedule.get("type")
        if stype == "at":
            return DateTrigger(run_date=_resolve_at_run_date(schedule, self._now_utc()))
        if stype == "interval":
            if record is not None and record.get("created_ts"):
                try:
                    anchor = datetime.fromisoformat(str(record["created_ts"]))
                except ValueError as exc:
                    _LOG.warning(
                        "scheduler: unparsable created_ts %r, interval job re-anchors"
                        " at now: %s",
                        record["created_ts"],
                        exc,
                    )
                else:
                    return IntervalTrigger(seconds=schedule["seconds"], start_date=anchor)
            return IntervalTrigger(seconds=schedule["seconds"])
        if stype == "cron":
            fields = _split_cron(schedule["expr"])
            try:
                # Positional: (second, minute, hour, day, month, day_of_week).
                return CronTrigger(
                    second=fields[0],
                    minute=fields[1],
                    hour=fields[2],
                    day=fields[3],
                    month=fields[4],
                    day_of_week=fields[5],
                    timezone=UTC,
                )
            except ValueError as exc:
                raise SchedulerError(f"invalid cron expr fields: {exc}") from exc
        raise SchedulerError(f"unknown schedule type {stype} (expected cron, interval, at)")

    # ------------------------------------------------------------------
    # scheduler.json persistence
    # ------------------------------------------------------------------
    def _load_jobs_file(self) -> list[dict]:
        """Read ``scheduler.json``; missing/corrupt => empty set + ONE warning.

        A corrupt file (invalid JSON, version != 1, missing ``jobs`` key)
        never blocks boot: it is treated as an empty job set.
        """
        path = self.jobs_path
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _LOG.warning("scheduler: unreadable %s treated as empty job set: %s", path, exc)
            return []
        if not isinstance(data, dict) or data.get("version") != 1 or "jobs" not in data:
            _LOG.warning("scheduler: %s has unknown layout treated as empty job set", path)
            return []
        jobs = data.get("jobs")
        if not isinstance(jobs, list):
            _LOG.warning("scheduler: %s jobs is not a list treated as empty job set", path)
            return []
        return [dict(job) for job in jobs if isinstance(job, dict)]

    def _persist_jobs(self) -> None:
        """Rewrite ``scheduler.json`` with the full merged in-memory registry."""
        jobs = []
        for _, record in sorted(self._jobs.items()):
            entry = dict(record)
            entry.setdefault("created_ts", self._now_iso())
            jobs.append(entry)
        _atomic_write_json(self.jobs_path, {"version": 1, "jobs": jobs})

    # ------------------------------------------------------------------
    # Runs ledger (append-only; failures logged, never raised)
    # ------------------------------------------------------------------
    def _append_ledger(self, row: dict) -> None:
        if self._ledger is None:
            self._ledger = JsonlWriter(self._ledger_path)
        try:
            self._ledger.append(row)
        except Exception as exc:  # noqa: BLE001 — a ledger failure never fails a job
            _LOG.error("scheduler: ledger append failed for job %s: %s", row.get("job_id"), exc)

    async def _finish(
        self, record: dict, outcome: str, detail: dict, *, publish: bool = True
    ) -> dict:
        """Append one ledger row, hand it to the sink seam, publish on the bus.

        The sink receives EXACTLY the ledger dict; a sink crash is logged, not
        raised. ``publish=False`` keeps restore-time misfire rows bus-silent.
        """
        row = {
            "ts": self._now_iso(),
            "job_id": record.get("id"),
            "job_kind": record.get("kind"),
            "outcome": outcome,
            "detail": detail,
        }
        self._append_ledger(row)
        if self._sink is not None:
            try:
                await self._sink(row)
            except Exception as exc:  # noqa: BLE001 — sink crash must not fail the job
                _LOG.warning("scheduler: sink failed for job %s: %s", row["job_id"], exc)
        if publish:
            await self._publish_fired(record, outcome)
        return row

    async def _publish_fired(self, record: dict, outcome: str) -> None:
        """One ``schedule.fired`` bus event per handler run (bus failure logged)."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                "schedule.fired",
                {"job_id": record.get("id"), "job_kind": record.get("kind"), "outcome": outcome},
            )
        except Exception as exc:  # noqa: BLE001 — bus failure must not fail the job
            _LOG.warning("scheduler: bus publish failed for job %s: %s", record.get("id"), exc)

    # ------------------------------------------------------------------
    # Job registration
    # ------------------------------------------------------------------
    def _ensure_sched(self) -> AsyncIOScheduler:
        """The engine scheduler, created lazily so add_job-before-start works
        (jobs register as pending until ``start()`` binds the loop)."""
        if self._sched is None:
            self._sched = AsyncIOScheduler(
                timezone=UTC, misfire_grace_time=self._grace, coalesce=self._coalesce
            )
        return self._sched

    def _register_job(self, record: dict) -> None:
        """Register one job on the (possibly pending) scheduler."""
        sched = self._ensure_sched()
        runner = _JobRunner(self, dict(record))
        sched.add_job(
            runner.run,
            self._build_trigger(record["schedule"], record),
            id=record["id"],
            misfire_grace_time=self._grace,
            coalesce=self._coalesce,
            max_instances=1,
            replace_existing=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Merge seed + restored jobs, register everything, persist, start.

        The merge is by id with the SEED config winning on conflicts (a
        warning is logged and the seed record replaces the stored one);
        ``created_ts`` is backfilled for seed jobs. ``scheduler.start()`` is
        called LAST so registration and persistence happen while the loop is
        not yet driving jobs. Every failure is wrapped in
        :class:`SchedulerError`.
        """
        try:
            sched = self._ensure_sched()
            merged: dict[str, dict] = {rid: dict(r) for rid, r in self._jobs.items()}
            for seed in self._cfg.scheduler.jobs:
                record = validate_job_spec(
                    dict(seed), max_desc_chars=int(self._cfg.tasks.max_desc_chars)
                )
                job_id = record["id"]
                if job_id in merged:
                    _LOG.warning(
                        "scheduler: seed job %s replaces the persisted record (seed wins)", job_id
                    )
                record.setdefault("created_ts", self._now_iso())
                merged[job_id] = record
            self._jobs = merged
            for record in merged.values():
                self._register_job(record)
            self._persist_jobs()
            sched.start()
        except Exception as exc:
            raise SchedulerError(f"scheduler start failed: {exc}") from exc

    async def restore(self) -> dict:
        """Boot-restore policy over ``scheduler.json`` (file -> registry only).

        Missing/corrupt file => empty set + one warning. Every stored job
        without ``created_ts`` is backfilled at load. ``at`` jobs with
        ``run_date < boot_now - misfire_grace_s`` are NOT re-registered: one
        ``misfire_skip`` ledger row is appended and the job is dropped from
        the persisted set (the file is kept, with an empty list when nothing
        survives). ``at`` within grace, ``cron`` and ``interval`` jobs are
        re-registered into the in-memory registry (the actual scheduler
        registration + seed-wins merge happens in :meth:`start`). The file is
        rewritten only when one existed. Never blocks boot: a job record that
        fails to resolve (corrupt entry) is dropped with a warning.
        """
        stored = self._load_jobs_file()
        boot_now = self._now_utc()
        kept: list[dict] = []
        restored = 0
        misfire_skipped = 0
        for raw in stored:
            record = dict(raw)
            record.setdefault("created_ts", self._now_iso())
            job_id = record.get("id")
            schedule = record.get("schedule")
            keep = False
            if isinstance(schedule, dict) and schedule.get("type") == "at":
                try:
                    run_date = _resolve_at_run_date(schedule, boot_now)
                except SchedulerError as exc:
                    _LOG.warning("scheduler: dropping corrupt stored job %s: %s", job_id, exc)
                else:
                    if run_date < boot_now - timedelta(seconds=self._grace):
                        misfire_skipped += 1
                        await self._finish(
                            record,
                            "misfire_skip",
                            {
                                "detail": (
                                    f"run_date {run_date.isoformat()} predates "
                                    f"boot_now - misfire_grace_s ({self._grace}s)"
                                )
                            },
                            publish=False,
                        )
                    else:
                        keep = True  # within grace: fires immediately at start (coalesce)
            else:
                keep = True  # cron/interval always re-registered
            if keep:
                self._jobs[job_id] = record
                kept.append(record)
                restored += 1
        if stored:
            self._persist_kept(kept)
        return {"restored": restored, "misfire_skipped": misfire_skipped}

    def _persist_kept(self, kept: list[dict]) -> None:
        """Rewrite ``scheduler.json`` with exactly ``kept`` (misfire-drop
        support: the file is kept, possibly with an empty jobs list)."""
        jobs = [dict(record) for record in kept]
        _atomic_write_json(self.jobs_path, {"version": 1, "jobs": jobs})

    async def stop(self) -> None:
        """Shut the scheduler down (idempotent; APScheduler errors logged)."""
        if self._sched is None:
            return
        try:
            self._sched.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001 — SchedulerNotYetAdded et al: log + continue
            _LOG.warning("scheduler: shutdown skipped (%s)", exc)
        self._sched = None

    # ------------------------------------------------------------------
    # Job mutations + views
    # ------------------------------------------------------------------
    async def add_job(self, spec: dict, *, persist: bool = True) -> dict:
        """Validate + register one job; return ``{"id", "kind", "created_ts"}``.

        Works before and after ``start()``: on a not-yet-started scheduler the
        job registers as pending. The job is persisted (default) so a later
        boot sees the same set.
        """
        record = validate_job_spec(dict(spec), max_desc_chars=int(self._cfg.tasks.max_desc_chars))
        job_id = record["id"]
        if job_id in self._jobs:
            raise SchedulerError(f"job id already exists: {job_id}")
        record["created_ts"] = self._now_iso()
        self._jobs[job_id] = record
        try:
            self._register_job(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._jobs.pop(job_id, None)  # never leave a half-registered job behind
            raise
        if persist:
            self._persist_jobs()
        return {"id": job_id, "kind": record["kind"], "created_ts": record["created_ts"]}

    async def remove_job(self, job_id: str, *, persist: bool = True) -> None:
        """Remove a job; unknown ids raise ``KeyError`` (WP3 maps to 404).

        APScheduler :class:`JobLookupError` (the scheduler lost the job ahead
        of the registry) is translated to ``KeyError`` as well; the merged
        in-memory registry is always popped.
        """
        if job_id not in self._jobs:
            raise KeyError(job_id)
        if self._sched is not None:
            try:
                self._sched.remove_job(job_id)
            except JobLookupError as exc:
                raise KeyError(job_id) from exc
        self._jobs.pop(job_id, None)
        if persist:
            self._persist_jobs()

    def list_jobs(self) -> list[dict]:
        """Registry snapshot sorted by id — schedule TYPE only, never the raw
        cron/datetime trigger internals."""
        return [
            {
                "id": record["id"],
                "kind": record["kind"],
                "created_ts": record.get("created_ts"),
                "schedule": {"type": record["schedule"].get("type")},
            }
            for job_id, record in sorted(self._jobs.items())
        ]

    # ------------------------------------------------------------------
    # Dispatch (the ONLY callable registered with APScheduler)
    # ------------------------------------------------------------------
    async def _run_job(self, record: dict) -> None:
        """Dispatch one fired job; errors become ``error`` ledger rows.

        Any non-Cancellation exception is swallowed after appending an
        ``error`` row (logged at ERROR + bus publish): a broken job must not
        crash the scheduler loop.
        """
        kind = record.get("kind")
        try:
            if kind == "spawn_agent":
                await self._handle_spawn_agent(record)
            elif kind == "run_script":
                await self._handle_run_script(record)
            elif kind == "drop_task":
                await self._handle_drop_task(record)
            else:
                await self._finish(
                    record, "error", {"error": f"unknown job kind {kind}"}
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — honest terminal, never crash the loop
            _LOG.error("scheduler: job %s failed: %s", record.get("id"), exc)
            await self._finish(record, "error", {"error": str(exc)})

    def _spawn_payload(self, record: dict) -> dict:
        """Kind payload handed to the handler overrides (defaults applied)."""
        kind = record["kind"]
        if kind == "spawn_agent":
            return {
                "prompt": str(record.get("prompt") or ""),
                "agent": str(record.get("agent") or "simple"),
                "channel": str(record.get("channel") or "schedule"),
            }
        if kind == "run_script":
            return {
                "argv": list(record["argv"]),
                "timeout_s": record.get("timeout_s") or self._cfg.scheduler.script_timeout_s,
            }
        return {"description": str(record.get("description") or "")}

    async def _handle_spawn_agent(self, record: dict) -> None:
        """spawn_agent: an ``AsyncIOScheduler``-driven cousin of the gateway
        chat path (session -> routing -> route -> agent_running -> relay task
        detached, ledger row AT HANDOFF)."""
        payload = self._spawn_payload(record)
        if self._spawn is not None:
            await self._spawn(record, payload)  # the override replaces the WHOLE default path
            await self._finish(record, "fired", {})
            return
        prompt_text = payload["prompt"]
        manager = self._manager
        if manager is None:
            await self._finish(record, "fired", {"detail": "no manager wired; spawn skipped"})
            return
        if self._router is None:  # NEVER leave a session mid-FSM
            await self._finish(record, "error", {"error": "no router wired"})
            return
        session = await manager.get_or_create(
            None, channel_handle=None, title=f"schedule:{record['id']}"
        )
        await manager.move(session, "routing")
        route = await self._router.route_async(
            prompt_text, session=session, session_manager=manager
        )
        await manager.move(session, "agent_running")
        adapter = self._resolve_adapter(route.agent)
        if adapter is None:
            # A failed spawn handoff is recorded in the ledger; the session is
            # intentionally left in agent_running (not terminal-safe to move).
            await self._finish(
                record, "error", {"error": f"no adapter wired for {route.agent!r}"}
            )
            return
        cfg_agent = getattr(self._cfg.agents, route.agent, None)
        spec = RunSpec(
            prompt=prompt_text,
            profile=None,
            workdir=None,
            resume_ref=session.agents_refs.get(route.agent),
            timeout_s=None if cfg_agent is None else cfg_agent.timeout_s,
        )
        task = asyncio.create_task(self._relay_run(session, adapter, spec, route))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await self._finish(record, "fired", {"session_id": session.id, "agent": route.agent})

    def _resolve_adapter(self, agent: str) -> object | None:
        """Adapter for ``agent`` (app.py ``_resolve_adapter`` duck-typing):
        a mapping entry (bare factory called with the name, adapter object
        used verbatim), a bare factory callable, or the registry; None when
        nothing resolves."""
        if self._adapters is None:
            try:
                return registry_get(agent, self._cfg)
            except KeyError:
                return None
        if callable(self._adapters):
            return self._adapters(agent)
        entry = self._adapters.get(agent)
        if entry is None:
            return None
        return entry(agent) if callable(entry) and not hasattr(entry, "run") else entry

    async def _relay_run(self, session, adapter, spec: RunSpec, route) -> None:
        """Relay one scheduled agent run (app.py ``_run_agent`` shape): stream
        events onto bus + transcript, then move ``relaying`` -> ``idle``."""
        started = time.perf_counter()
        seq = 0

        async def _chunk(text: str) -> None:
            nonlocal seq
            seq += 1
            if self._bus is not None:
                await self._bus.publish(
                    "reply.chunk",
                    {"session_id": session.id, "seq": seq, "text": text, "source": "agent"},
                )
            await session.append_event(
                "agent_chunk", {"seq": seq, "text": text, "source": "agent"}
            )

        async def _final(text: str, *, failed: bool) -> None:
            meta: dict = {
                "agent": route.agent,
                "cost": None,
                "duration_s": round(time.perf_counter() - started, 3),
            }
            if failed:
                meta["failed"] = True
            if self._bus is not None:
                await self._bus.publish(
                    "reply.final", {"session_id": session.id, "text": text, "meta": meta}
                )
            await session.append_event("agent_final", {"text": text, "meta": meta})

        terminal_seen = False
        try:
            async for event in adapter.run(spec):
                if event.kind == "chunk":
                    await _chunk(event.text or "")
                elif event.kind == "tool":
                    brief = event.tool_brief or event.tool_name or "?"
                    await _chunk(f"[tool] {brief}")
                elif event.kind == "final":
                    await _final(event.final_text or "", failed=False)
                    terminal_seen = True
                    break
                elif event.kind == "failed":
                    await _final(f"[run failed] {event.error or 'unknown error'}", failed=True)
                    terminal_seen = True
                    break
                elif event.kind in ("started", "system"):
                    continue  # not relayed (documented PoC contract)
            if not terminal_seen:
                await _final("[run failed] adapter stream ended without a final event", failed=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — honest terminal, never crash
            _LOG.exception("scheduler: agent run failed for session %s", session.id)
            await _final(f"[run failed] internal error: {exc}", failed=True)
        finally:
            try:
                await self._manager.move(session, "relaying")
                await self._manager.move(session, "idle")
            except SessionFSMError as exc:
                _LOG.warning("session %s post-run moves skipped: %s", session.id, exc)

    async def _handle_run_script(self, record: dict) -> None:
        """run_script: argv-only child run (NO shell), output never stored;
        allowlisted supervisor env names are passed through to the child env."""
        payload = self._spawn_payload(record)
        if self._script is not None:
            await self._script(record, payload)
            await self._finish(record, "fired", {})
            return
        timeout_s = payload["timeout_s"]
        names = [str(name) for name in (self._cfg.scheduler.env_allowlist or []) if str(name)]
        extra: dict[str, str] = {}
        for name in names:
            if name.startswith("HARNESS_"):
                if name in os.environ:
                    _LOG.warning(
                        "scheduler: run_script env_allowlist entry %r matches the HARNESS_*"
                        " deny prefix (config-authoring bug); not passed through",
                        name,
                    )
                continue
            if name in os.environ:
                extra[name] = os.environ[name]
        env = build_child_env(os.environ, extra=extra)
        proc, _ = await start_process(list(record["argv"]), cwd=str(self._data_dir), env=env)
        try:
            out, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            await kill_process_group(proc)
            await self._finish(
                record, "error", {"error": f"script timed out after {timeout_s}s"}
            )
            return
        await self._finish(
            record,
            "fired",
            {
                "returncode": proc.returncode,
                "stdout_bytes": len(out),
                "stderr_bytes": len(err_bytes),
            },
        )

    async def _handle_drop_task(self, record: dict) -> None:
        """drop_task: one inbox task with ``origin="scheduler"`` via a lazily
        constructed (and cached) :class:`~harness.tasks.TaskStore`."""
        payload = self._spawn_payload(record)
        if self._drop is not None:
            await self._drop(record, payload)
            await self._finish(record, "fired", {})
            return
        if self._task_store is None:
            self._task_store = TaskStore(self._cfg, bus=self._bus)
        task_id = self._task_store.create_inbox_task(payload["description"], origin="scheduler")
        await self._finish(record, "fired", {"task_id": task_id})

    # ------------------------------------------------------------------
    # WP4 demand seam (drop-block schedule demands -> derived jobs)
    # ------------------------------------------------------------------
    async def submit_demand(self, demand: dict) -> dict:
        """WP4 seam: accept a drop-demand payload and register a derived one-shot job.

        ``demand`` is the dict the TaskLoop scheduler sink forwards
        (``task_id``/``schedule``/``description``/``origin_task``); the engine
        keeps only the job-relevant slice (``description`` + ``schedule``) and
        translates it into a ``drop_task`` job spec whose ``schedule`` passes
        the SAME :func:`validate_job_spec` path as every other job (a malformed
        demand raises :class:`SchedulerError` from :meth:`add_job`). Job ids are
        the task-id grammar ``[A-Z2-7]{8}`` with an ``SD`` prefix, drawn from
        the RFC 4648 base32 alphabet (every char of the alphabet is inside
        ``A-Z2-7``, unlike hex digits 0/1).
        """
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        job_id = "SD" + "".join(secrets.choice(alphabet) for _ in range(6))
        # Local regex assert: the generated id MUST match the task-id grammar.
        if re.fullmatch(r"[A-Z2-7]{8}", job_id) is None:
            raise SchedulerError(f"demand job id {job_id!r} failed the [A-Z2-7]{{8}} grammar")
        spec = {
            "id": job_id,
            "kind": "drop_task",
            "description": str(demand.get("description") or ""),
            "schedule": demand.get("schedule") or {},
        }
        return await self.add_job(spec, persist=True)
