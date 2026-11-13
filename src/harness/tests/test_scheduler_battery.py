"""R11 WP5: the row-20 fake-clock E2E battery for the scheduler engine
(:mod:`harness.scheduler`).

One module, local clones only (no cross-test imports; the ``_b`` prefix
avoids collisions with tests/test_scheduler.py if both were ever imported
into one process). The VALIDATED FAKE-CLOCK RECIPE IS FIVE NAMESPACES:
``apscheduler.schedulers.base``, ``apscheduler.triggers.{interval,cron,date}``
AND ``apscheduler.executors.base`` (where the executor-side misfire check
lives) patched with a ``FakeDT`` subclass whose ``now`` returns the SAME
``_BClock`` instance the engine received as ``clock=``, then drive the
running scheduler with ``wakeup()`` after clock advances. The fake epoch is
anchored to the real UTC clock at second resolution (instead of a hardcoded
calendar date) so the design relations (t0+75s, boot-30s, 23:59:40-of-day
...) hold exactly. Every test that lets real wall-time-vs-fake-time
divergence matter enters ALL FIVE contexts (row 6 — its dispatched
``run_time`` must be judged stale by the executor grace check against fake
time); the other rows enter the first four, where every dispatched
``run_time`` is within grace under either wall.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import apscheduler.executors.base as ex_mod
import apscheduler.schedulers.base as base_mod
import apscheduler.triggers.cron as cr_mod
import apscheduler.triggers.date as date_mod
import apscheduler.triggers.interval as iv_mod
import pytest
import yaml
from fastapi.testclient import TestClient

from harness.adapters.base import StreamEvent
from harness.blocks import parse_blocks
from harness.bus import Bus
from harness.gateway.app import create_app
from harness.persist import read_jsonl
from harness.router import RouterBrain
from harness.scheduler import SchedulerEngine
from harness.session_manager import SessionManager
from harness.taskloop import TaskLoop
from harness.tasks import TaskDir, TaskStore
from tests.mocks.router_model import make_fake_model

API_KEY = "test-key-123"  # conftest tmp_volume feeds HARNESS_API_KEY with this
HEADERS = {"X-API-Key": API_KEY}

# The env-dump job script for row 3 (wrapper FILE, apostrophe-free double
# quoting inside): dumps sorted(os.environ) keys to the R11_OUTFILE path.
_SCRIPT_DUMP_ENV = """\
import os
keys = sorted(os.environ)
with open(os.environ["R11_OUTFILE"], "w") as fh:
    fh.write(repr(keys))
"""

_P1 = "ADA23456"


class _BClock:
    """FakeClock-equivalent (engine ``clock=`` injection shape): now(tz) /
    advance(seconds) over a controllable aware start datetime."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self, tz: Any | None = None) -> datetime:
        return self._now if tz is None else self._now.astimezone(tz)


def _b_patch(clock: _BClock) -> tuple[Any, Any, Any, Any, Any]:
    """The validated FIVE-namespace fake-clock recipe: the four scheduler-side
    namespaces (scratch probe14/16) plus ``apscheduler.executors.base``.
    Tests enter ``p[4]`` only when real-wall-vs-fake divergence matters."""

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz: Any | None = None) -> datetime:
            return clock.now(tz)

    return (
        mock.patch.object(base_mod, "datetime", FakeDT),
        mock.patch.object(iv_mod, "datetime", FakeDT),
        mock.patch.object(cr_mod, "datetime", FakeDT),
        mock.patch.object(date_mod, "datetime", FakeDT),
        mock.patch.object(ex_mod, "datetime", FakeDT),
    )


async def _b_drive(clock: _BClock, sched: Any, seconds: float) -> None:
    """Advance the fake clock and wake the running scheduler once."""
    clock.advance(seconds)
    sched.wakeup()
    await asyncio.sleep(0.05)


def _b_real_boot() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _b_bus_recorder() -> Bus:
    return Bus()


def _b_engine(cfg: Any, tmp_volume: Path, clock: _BClock | None = None, **kw: Any) -> Any:
    """Battery build-under-test: the real engine against tmp_volume state."""
    bus = kw.pop("bus", None) or _b_bus_recorder()
    return SchedulerEngine(
        cfg,
        bus=bus,
        manager=None,
        clock=clock,
        jobs_file=str(tmp_volume / "state" / "scheduler.json"),
        **kw,
    )


def _b_ledger(cfg: Any) -> Path:
    return Path(cfg.storage.data_dir) / "state" / "scheduler_runs_ledger.jsonl"


async def _b_spin_until(predicate: Any, attempts: int = 100, tick: float = 0.05) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(tick)
    return predicate()


def _b_make_parent(store: TaskStore, cfg: Any, task_id: str, description: str) -> TaskDir:
    store.create_inbox_task(description, origin="user", task_id=task_id)
    destination = store.move_from_inbox(task_id)
    return TaskDir.birth(destination, cfg)


def _b_block(text: str) -> Any:
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    return blocks[0]


def _b_drop_text(description: str, schedule_yaml: str = "") -> str:
    return "```SUBTASK_DROP\n" + f"description: {description}\n" + schedule_yaml + "```"


def _b_events(store: TaskStore, task_id: str) -> list[dict]:
    return read_jsonl(store.root / task_id / "events.jsonl")


def _b_kinds(store: TaskStore, task_id: str) -> list[str]:
    return [event["kind"] for event in _b_events(store, task_id)]


# ---------------------------------------------------------------------------
# Row 1: spawn_agent E2E over the fake clock (restore -> start -> fire)
# ---------------------------------------------------------------------------
async def test_battery_spawn_agent_e2e_fakeclock(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        bus = _b_bus_recorder()
        queue = bus.subscribe("b-battery")
        calls: list[tuple[dict, dict]] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append((dict(record), dict(payload)))

        engine = _b_engine(cfg, tmp_volume, clock, bus=bus, spawn=spawn)
        restored = await engine.restore()
        assert restored == {"restored": 0, "misfire_skipped": 0}
        created = await engine.add_job(
            {
                "id": "bat1",
                "kind": "spawn_agent",
                "prompt": "battery prompt for the coder",
                "schedule": {"type": "at", "iso": (boot - timedelta(seconds=30)).isoformat()},
            },
            persist=False,
        )
        assert created["created_ts"] == boot.isoformat()
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        assert await _b_spin_until(lambda: len(calls) == 1), "spawn never fired"
        await engine.stop()

    assert len(calls) == 1
    record, payload = calls[0]
    assert record["id"] == "bat1" and record["kind"] == "spawn_agent"
    assert payload == {
        "prompt": "battery prompt for the coder",
        "agent": "simple",
        "channel": "schedule",
    }
    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "fired" and row["job_id"] == "bat1"
    assert row["job_kind"] == "spawn_agent" and row["detail"] == {}
    assert row["ts"] == (boot + timedelta(seconds=0.1)).isoformat()
    topic, payload_b = await asyncio.wait_for(queue.get(), timeout=5)
    assert topic == "schedule.fired"
    assert payload_b == {"job_id": "bat1", "job_kind": "spawn_agent", "outcome": "fired"}


# ---------------------------------------------------------------------------
# Row 2: run_script E2E (byte-count ledger detail, no stdout content)
# ---------------------------------------------------------------------------
async def test_battery_run_script_e2e_fakeclock(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        engine = _b_engine(cfg, tmp_volume, clock)
        await engine.restore()
        await engine.add_job(
            {
                "id": "bat2",
                "kind": "run_script",
                "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
                "argv": [sys.executable, "-c", "print(42)"],
            },
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        assert await _b_spin_until(
            lambda: len(read_jsonl(_b_ledger(cfg))) == 1
        ), "script job never fired"
        await engine.stop()

    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "fired" and row["job_kind"] == "run_script"
    detail = row["detail"]
    assert detail["returncode"] == 0 and detail["stdout_bytes"] > 0
    assert "42" not in json.dumps(detail)  # byte counts only, never script output


# ---------------------------------------------------------------------------
# Row 3a: HARNESS_API_KEY denied, allowlisted env passes through (design
# contract: cfg.scheduler.env_allowlist copies the named supervisor env vars
# into the run_script child env via build_child_env extra=)
# ---------------------------------------------------------------------------
async def test_battery_run_script_harness_key_denied(
    tmp_volume, cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    outfile = tmp_volume / "envdump_pass.txt"
    script = tmp_volume / "dump_env_pass.py"
    tmp_volume.mkdir(parents=True, exist_ok=True)
    script.write_text(_SCRIPT_DUMP_ENV, encoding="utf-8")
    monkeypatch.setenv("HARNESS_API_KEY", "sentinel-battery-key")
    monkeypatch.setenv("R11_MARKER", "v1")
    monkeypatch.setenv("R11_OUTFILE", str(outfile))
    cfg = cfg.model_copy(
        update={"scheduler": cfg.scheduler.model_copy(
            update={"env_allowlist": ["R11_MARKER", "R11_OUTFILE"]})}
    )
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        engine = _b_engine(cfg, tmp_volume, clock)
        await engine.restore()
        await engine.add_job(
            {
                "id": "bat3a",
                "kind": "run_script",
                "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
                "argv": [sys.executable, str(script)],
            },
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        assert await _b_spin_until(
            lambda: len(read_jsonl(_b_ledger(cfg))) == 1
        ), "script job never fired"
        await engine.stop()

    rows = read_jsonl(_b_ledger(cfg))
    assert rows[0]["outcome"] == "fired"
    assert "sentinel-battery-key" not in json.dumps(rows[0])  # secret never in the ledger
    keys = ast.literal_eval(outfile.read_text(encoding="utf-8"))
    assert "HARNESS_API_KEY" not in keys
    assert "R11_OUTFILE" in keys and "R11_MARKER" in keys  # allowlisted -> passthrough


# ---------------------------------------------------------------------------
# Row 3b: a name NOT on the allowlist is not passed through
# ---------------------------------------------------------------------------
async def test_battery_run_script_env_allowlist_not_passed(
    tmp_volume, cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    outfile = tmp_volume / "envdump_deny.txt"
    script = tmp_volume / "dump_env_deny.py"
    tmp_volume.mkdir(parents=True, exist_ok=True)
    script.write_text(_SCRIPT_DUMP_ENV, encoding="utf-8")
    monkeypatch.setenv("HARNESS_API_KEY", "sentinel-battery-key")
    monkeypatch.setenv("R11_MARKER", "v1")
    monkeypatch.setenv("R11_OUTFILE", str(outfile))
    cfg = cfg.model_copy(
        update={"scheduler": cfg.scheduler.model_copy(update={"env_allowlist": ["R11_OUTFILE"]})}
    )
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        engine = _b_engine(cfg, tmp_volume, clock)
        await engine.restore()
        await engine.add_job(
            {
                "id": "bat3b",
                "kind": "run_script",
                "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
                "argv": [sys.executable, str(script)],
            },
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        assert await _b_spin_until(
            lambda: len(read_jsonl(_b_ledger(cfg))) == 1
        ), "script job never fired"
        await engine.stop()

    keys = ast.literal_eval(outfile.read_text(encoding="utf-8"))
    assert "R11_OUTFILE" in keys  # allowlisted -> passthrough works
    assert "R11_MARKER" not in keys  # not allowlisted -> gated


# ---------------------------------------------------------------------------
# Row 4: drop_task E2E -> inbox task, origin=scheduler (task.yaml + state)
# ---------------------------------------------------------------------------
async def test_battery_drop_task_e2e_fakeclock(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        engine = _b_engine(cfg, tmp_volume, clock)
        await engine.restore()
        await engine.add_job(
            {
                "id": "bat4",
                "kind": "drop_task",
                "description": "battery scheduled follow-up",
                "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
            },
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        store = TaskStore(cfg)
        assert await _b_spin_until(lambda: bool(store.list_inbox())), "inbox never appeared"
        await engine.stop()

    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1 and rows[0]["outcome"] == "fired" and rows[0]["job_kind"] == "drop_task"
    task_id = rows[0]["detail"]["task_id"]
    assert store.list_inbox() == [task_id]
    task_dir = Path(cfg.storage.data_dir) / "tasks" / "inbox" / task_id
    assert task_dir.is_dir()
    raw = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    assert raw["description"] == "battery scheduled follow-up" and raw["origin"] == "scheduler"
    destination = store.move_from_inbox(task_id)
    TaskDir.birth(destination, cfg, origin="scheduler")
    state = store.read_state(task_id)
    assert state is not None and state["origin"] == "scheduler"


# ---------------------------------------------------------------------------
# Row 5: 60s interval + advance(130) -> coalesced to exactly ONE fire
# ---------------------------------------------------------------------------
async def test_battery_f16_coalesce_missed_to_one(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))

        engine = _b_engine(cfg, tmp_volume, clock, spawn=spawn)
        await engine.restore()
        await engine.add_job(
            {"id": "bat5", "kind": "spawn_agent",
             "schedule": {"type": "interval", "seconds": 60}},
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 130)
        await asyncio.sleep(0.08)
        await engine.stop()
    rows = read_jsonl(_b_ledger(cfg))
    assert calls == ["bat5"], calls
    assert len(rows) == 1 and rows[0]["outcome"] == "fired"


# ---------------------------------------------------------------------------
# Row 6: a pending run dispatched beyond the fake grace window fires ZERO
# times (executor misfire check judged against fake time — the 4-namespace
# recipe cannot pin this: real wall time trails the fake runs, so a plain
# interval's coalesced run_time — always < one interval stale — fires; the
# at-kind misfire path pins the design instead: run_date staleness > grace).
# ---------------------------------------------------------------------------
async def test_battery_f16_beyond_grace_zero_fires(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3], p[4]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))

        engine = _b_engine(cfg, tmp_volume, clock, spawn=spawn)
        await engine.restore()
        await engine.add_job(
            {"id": "bat6", "kind": "spawn_agent",
             "schedule": {"type": "at", "iso": (boot + timedelta(seconds=60)).isoformat()}},
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 130)  # run_date now 70s beyond the 60s grace
        await asyncio.sleep(0.08)
        assert calls == [] and read_jsonl(_b_ledger(cfg)) == []  # the beyond-grace premise
        await _b_drive(clock, engine._sched, 600)  # still pending beyond grace: zero fires
        await asyncio.sleep(0.08)
        await engine.stop()
    assert calls == [], calls
    assert read_jsonl(_b_ledger(cfg)) == []


# ---------------------------------------------------------------------------
# Row 7: max_instances=1 skips the overlapping fire; the engine survives
# ---------------------------------------------------------------------------
async def test_battery_f16_max_instances_skips_second(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))
            await asyncio.sleep(0.2)

        engine = _b_engine(cfg, tmp_volume, clock, spawn=spawn)
        await engine.restore()
        await engine.add_job(
            {"id": "bat7", "kind": "spawn_agent",
             "schedule": {"type": "interval", "seconds": 1}},
            persist=False,
        )
        await engine.start()
        await _b_drive(clock, engine._sched, 1)
        await _b_drive(clock, engine._sched, 1)
        await asyncio.sleep(0.08)
        assert len(calls) == 1, calls  # second submission skipped (max_instances=1)
        await asyncio.sleep(0.25)  # let the slow first fire finish
        await _b_drive(clock, engine._sched, 1)
        assert await _b_spin_until(lambda: len(calls) == 2), "job never fired again"
        await asyncio.sleep(0.25)  # let the second fire finish before shutdown
        await engine.stop()
    assert [job["id"] for job in engine.list_jobs()] == ["bat7"]  # engine still functional


# ---------------------------------------------------------------------------
# Row 8: boot-restore of an interval job lands on the next boundary
# ---------------------------------------------------------------------------
async def test_battery_boot_restore_interval_lands_on_boundary(tmp_volume, cfg) -> None:
    t0 = _b_real_boot()
    clock1 = _BClock(t0)
    p1 = _b_patch(clock1)
    calls: list[str] = []

    async def spawn(record: dict, payload: dict) -> None:
        calls.append(record.get("id"))

    with p1[0], p1[1], p1[2], p1[3]:
        engine_a = _b_engine(cfg, tmp_volume, clock1, spawn=spawn)
        await engine_a.add_job(
            {"id": "bat8", "kind": "spawn_agent",
             "schedule": {"type": "interval", "seconds": 30}}
        )
        await engine_a.stop()  # write happens automatically at add_job

    clock2 = _BClock(t0 + timedelta(seconds=75))
    p2 = _b_patch(clock2)
    with p2[0], p2[1], p2[2], p2[3]:
        engine_b = _b_engine(cfg, tmp_volume, clock2, spawn=spawn)
        restored = await engine_b.restore()
        assert restored["restored"] >= 1
        await engine_b.start()
        await _b_drive(clock2, engine_b._sched, 9)
        assert len(calls) == 0, calls  # nothing fires before the boundary
        await _b_drive(clock2, engine_b._sched, 6)
        assert len(calls) == 1, calls  # the next 30s boundary (t0+90)
        await engine_b.stop()
    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1 and rows[0]["outcome"] == "fired" and rows[0]["job_id"] == "bat8"


# ---------------------------------------------------------------------------
# Row 9: boot-restore misfire policy (run_date beyond grace -> skip)
# ---------------------------------------------------------------------------
async def test_battery_boot_restore_misfire_skip(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    path = tmp_volume / "state" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "bat9",
                        "kind": "spawn_agent",
                        "created_ts": boot.isoformat(),
                        "schedule": {"type": "at",
                                     "iso": (boot - timedelta(seconds=3600)).isoformat()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = _b_engine(cfg, tmp_volume, clock)
    result = await engine.restore()
    assert result == {"restored": 0, "misfire_skipped": 1}
    assert "bat9" not in [job["id"] for job in engine.list_jobs()]
    await engine.stop()
    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "misfire_skip" and row["job_id"] == "bat9"
    assert row["ts"] == boot.isoformat()


# ---------------------------------------------------------------------------
# Row 10: boot-restore of a within-grace at-job fires exactly once at start
# ---------------------------------------------------------------------------
async def test_battery_boot_restore_within_grace_fires_once(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    path = tmp_volume / "state" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "bat10",
                        "kind": "spawn_agent",
                        "created_ts": boot.isoformat(),
                        "prompt": "within-grace one-shot",
                        "schedule": {"type": "at",
                                     "iso": (boot - timedelta(seconds=30)).isoformat()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))

        engine = _b_engine(cfg, tmp_volume, clock, spawn=spawn)
        result = await engine.restore()
        assert result == {"restored": 1, "misfire_skipped": 0}
        await engine.start()
        await _b_drive(clock, engine._sched, 0.1)
        await asyncio.sleep(0.08)
        await engine.stop()
    assert calls == ["bat10"]
    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1 and rows[0]["outcome"] == "fired"


# ---------------------------------------------------------------------------
# Row 11: cron boot -> first fire at the next cron boundary, not before
# ---------------------------------------------------------------------------
async def test_battery_cron_boot_next_boundary(tmp_volume, cfg) -> None:
    today = datetime.now(UTC).date()
    boot = datetime.combine(today, time(23, 59, 40), tzinfo=UTC)
    clock = _BClock(boot)
    path = tmp_volume / "state" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "bat11",
                        "kind": "spawn_agent",
                        "created_ts": boot.isoformat(),
                        "schedule": {"type": "cron", "expr": "50 59 23 * * *"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))

        engine = _b_engine(cfg, tmp_volume, clock, spawn=spawn)
        restored = await engine.restore()
        assert restored == {"restored": 1, "misfire_skipped": 0}
        await engine.start()
        await _b_drive(clock, engine._sched, 9)
        assert len(calls) == 0, calls  # not before 23:59:50
        await _b_drive(clock, engine._sched, 6)
        await asyncio.sleep(0.08)
        await engine.stop()
    assert calls == ["bat11"], calls
    rows = read_jsonl(_b_ledger(cfg))
    assert len(rows) == 1 and rows[0]["outcome"] == "fired"
    assert rows[0]["job_kind"] == "spawn_agent"


# ---------------------------------------------------------------------------
# Row 12 (replacement): corrupt jobs_file treated as an empty job set; boot
# still works (seed jobs from cfg register through start())
# ---------------------------------------------------------------------------
async def test_battery_jobs_file_corrupt_treated_empty(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    path = tmp_volume / "state" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x01not json at all{{{")
    seed = {
        "id": "bat12",
        "kind": "spawn_agent",
        "prompt": "seed through the corrupt boot",
        "schedule": {"type": "at", "iso": (boot - timedelta(seconds=30)).isoformat()},
    }
    cfg_seeded = cfg.model_copy(
        update={"scheduler": cfg.scheduler.model_copy(update={"jobs": [seed]})}
    )
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        calls: list[str] = []

        async def spawn(record: dict, payload: dict) -> None:
            calls.append(record.get("id"))

        engine = _b_engine(cfg_seeded, tmp_volume, clock, spawn=spawn)
        result = await engine.restore()  # must not raise
        assert result == {"restored": 0, "misfire_skipped": 0}
        await engine.start()  # empty restored set + seed jobs still register
        await _b_drive(clock, engine._sched, 0.1)
        await asyncio.sleep(0.08)
        await engine.stop()
    assert calls == ["bat12"], calls
    assert [job["id"] for job in engine.list_jobs()] == ["bat12"]


# ---------------------------------------------------------------------------
# Row 13: CRUD roundtrip through the REAL engine behind the gateway app
# ---------------------------------------------------------------------------
class _BStubAdapter:
    """ScriptedAdapter-like fake adapter (replays one final event)."""

    async def run(self, spec: Any) -> Any:
        yield StreamEvent(kind="final", final_text="battery final")


def _b_make_app(cfg: Any, scheduler_: Any) -> Any:
    bus = Bus()
    manager = SessionManager(cfg, bus=bus)
    brain = RouterBrain(cfg, model=make_fake_model([{"agent": "opencode"}]))
    adapters = {"opencode": _BStubAdapter()}
    return create_app(
        cfg, bus, manager, router_brain=brain, adapters=adapters, scheduler_=scheduler_
    )


async def test_battery_crud_roundtrip_api_level(tmp_volume, cfg) -> None:
    engine = _b_engine(cfg, tmp_volume, clock=None)
    await engine.restore()
    await engine.start()  # started: add/remove go through the real jobstore
    app = _b_make_app(cfg, engine)
    jobs_path = tmp_volume / "state" / "scheduler.json"
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/schedule",
                headers=HEADERS,
                json={
                    "id": "BBB23456",
                    "kind": "drop_task",
                    "schedule": {"type": "at", "iso": "+30s"},
                    "description": "battery api drop",
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["ok"] is True
            assert body["job"]["id"] == "BBB23456" and body["job"]["kind"] == "drop_task"
            listed = client.get("/api/schedule", headers=HEADERS)
            assert listed.status_code == 200
            jobs = listed.json()["jobs"]
            assert [job["id"] for job in jobs] == ["BBB23456"]
            assert jobs[0]["schedule"] == {"type": "at"}
            on_disk = json.loads(jobs_path.read_text(encoding="utf-8"))
            assert [job["id"] for job in on_disk["jobs"]] == ["BBB23456"]
            deleted = client.delete("/api/schedule/BBB23456", headers=HEADERS)
            assert deleted.status_code == 200
            assert deleted.json() == {"ok": True, "job_id": "BBB23456"}
            listed_after = client.get("/api/schedule", headers=HEADERS)
            assert listed_after.json() == {"jobs": []}
            assert json.loads(jobs_path.read_text(encoding="utf-8")) == {"version": 1, "jobs": []}
    finally:
        await engine.stop()


# ---------------------------------------------------------------------------
# Row 14: the materials report carries the scheduler section
# ---------------------------------------------------------------------------
async def test_battery_materials_report_scheduler_section(tmp_volume, cfg) -> None:
    engine = _b_engine(cfg, tmp_volume, clock=None)
    await engine.add_job(
        {
            "id": "CCC23456",
            "kind": "run_script",
            "schedule": {"type": "interval", "seconds": 30},
            "argv": ["true"],
        },
        persist=False,
    )
    app = _b_make_app(cfg, engine)
    with TestClient(app) as client:
        response = client.get("/materials?format=json", headers=HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["schedule_jobs"] == [
            {"name": "CCC23456", "next_ts": None, "kind": "run_script"}
        ]


# ---------------------------------------------------------------------------
# Row 15: drop-demand sink -> derived SD job -> scheduler inbox child
# ---------------------------------------------------------------------------
async def test_battery_demand_sink_e2e(tmp_volume, cfg) -> None:
    boot = _b_real_boot()
    clock = _BClock(boot)
    p = _b_patch(clock)
    with p[0], p[1], p[2], p[3]:
        engine = _b_engine(cfg, tmp_volume, clock)
        await engine.restore()
        await engine.start()
        store = TaskStore(cfg)
        parent = _b_make_parent(store, cfg, _P1, "battery demand parent")
        block = _b_block(
            _b_drop_text("battery demand child", "schedule:\n  type: at\n  iso: +5s\n")
        )
        loop = TaskLoop(cfg, SessionManager(cfg), scheduler_sink=engine.submit_demand)
        await loop._handle_drop(parent, block)

        kinds = _b_kinds(store, _P1)
        assert "subtask_schedule_accepted" in kinds and "subtask_drop" in kinds
        jobs = engine.list_jobs()
        assert len(jobs) == 1 and jobs[0]["id"].startswith("SD")
        assert jobs[0]["kind"] == "drop_task" and jobs[0]["schedule"] == {"type": "at"}
        assert read_jsonl(_b_ledger(cfg)) == []  # not yet due
        await _b_drive(clock, engine._sched, 6)
        assert await _b_spin_until(
            lambda: len(read_jsonl(_b_ledger(cfg))) == 1
        ), "derived job never fired"
        await engine.stop()

    rows = read_jsonl(_b_ledger(cfg))
    assert rows[0]["outcome"] == "fired" and rows[0]["job_kind"] == "drop_task"
    assert rows[0]["job_id"].startswith("SD")
    assert rows[0]["ts"] == (boot + timedelta(seconds=6)).isoformat()
    children = [
        entry
        for entry in store.list_inbox()
        if yaml.safe_load(
            (store.inbox / entry / "task.yaml").read_text(encoding="utf-8")
        )["origin"]
        == "scheduler"
    ]
    assert len(children) == 1
    raw = yaml.safe_load((store.inbox / children[0] / "task.yaml").read_text(encoding="utf-8"))
    assert raw["description"] == "battery demand child"
    assert rows[0]["detail"] == {"task_id": children[0]}
