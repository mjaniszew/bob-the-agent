"""Tests for harness.scheduler (R11 WP1): job-spec validation, the engine's
add/list/remove/persist/restore surface, the runs ledger + sink seam + bus
publish contract and the drop_task -> inbox path (TEST_PLAN row 20, first
column — WP5 carries the full battery)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.bus import Bus
from harness.persist import read_jsonl
from harness.scheduler import SchedulerEngine, SchedulerError, validate_job_spec
from harness.tasks import TaskDir, TaskStore


class _FakeClock:
    """FakeClock-equivalent (engine `clock=` injection shape): now(tz=None) /
    advance(seconds) over a controllable aware start datetime."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self, tz: Any | None = None) -> datetime:
        return self._now if tz is None else self._now.astimezone(tz)


def _jobs_file(cfg) -> Path:
    return Path(cfg.storage.data_dir) / "state" / "scheduler.json"


def _ledger_file(cfg) -> Path:
    return Path(cfg.storage.data_dir) / "state" / "scheduler_runs_ledger.jsonl"


async def _spin_until(predicate, attempts: int = 100, tick: float = 0.05) -> bool:
    """Poll a condition without sleeps-based timing assumptions (5s budget)."""
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(tick)
    return predicate()


# ---------------------------------------------------------------------------
# 1. validate_job_spec
# ---------------------------------------------------------------------------
def test_validate_job_spec_accepts_all_kinds() -> None:
    spawn = {
        "id": "spawn-1",
        "kind": "spawn_agent",
        "schedule": {"type": "cron", "expr": "0 0 * * * *"},
        "prompt": "check the queue",
    }
    script = {
        "id": "script-1",
        "kind": "run_script",
        "schedule": {"type": "interval", "seconds": 30},
        "argv": ["echo", "hi"],
    }
    drop = {
        "id": "drop-1",
        "kind": "drop_task",
        "schedule": {"type": "at", "iso": "+30s"},
        "description": "follow up",
    }
    snapshot = (dict(spawn), dict(script), dict(drop))
    out_spawn = validate_job_spec(spawn)
    out_script = validate_job_spec(script)
    out_drop = validate_job_spec(drop)
    # The ORIGINAL dict is returned as a copy and never mutated (id verbatim).
    assert out_spawn == spawn and out_spawn is not spawn
    assert out_script == script and out_drop == drop
    assert (dict(spawn), dict(script), dict(drop)) == snapshot
    assert out_spawn["id"] == "spawn-1" and out_drop["id"] == "drop-1"


def test_validate_job_spec_rejects_unknown_kind() -> None:
    with pytest.raises(SchedulerError, match="unknown job kind"):
        validate_job_spec({"id": "x", "kind": "summon_demon", "schedule": {"type": "interval",
                                                                            "seconds": 5}})


def test_validate_job_spec_rejects_bad_cron_field_count() -> None:
    with pytest.raises(SchedulerError, match="cron expr must have 6 fields"):
        validate_job_spec(
            {"id": "x", "kind": "spawn_agent", "schedule": {"type": "cron", "expr": "0 9 * * *"}}
        )
    ok = validate_job_spec(
        {"id": "x", "kind": "spawn_agent", "schedule": {"type": "cron", "expr": "0 0 9 * * *"}}
    )
    assert ok["id"] == "x"
    sh = validate_job_spec(
        {"id": "y", "kind": "spawn_agent", "schedule": {"type": "cron", "expr": "@daily"}}
    )
    assert sh["schedule"]["expr"] == "@daily"


def test_validate_job_spec_rejects_bad_interval() -> None:
    for seconds in (0, -5):
        with pytest.raises(SchedulerError, match="interval.seconds must be > 0"):
            validate_job_spec(
                {"id": "x", "kind": "run_script", "schedule": {"type": "interval",
                                                               "seconds": seconds},
                 "argv": ["true"]}
            )


def test_validate_job_spec_rejects_naive_at_iso() -> None:
    with pytest.raises(SchedulerError, match="at.iso must be timezone-aware ISO datetime"):
        validate_job_spec(
            {"id": "x", "kind": "drop_task", "schedule": {"type": "at",
                                                          "iso": "2026-01-01T00:00:00"},
             "description": "d"}
        )
    aware = validate_job_spec(
        {"id": "x", "kind": "drop_task",
         "schedule": {"type": "at", "iso": "2026-01-01T00:00:00+00:00"}, "description": "d"}
    )
    assert aware["id"] == "x"
    relative = validate_job_spec(
        {"id": "y", "kind": "drop_task", "schedule": {"type": "at", "iso": "+90s"},
         "description": "d"}
    )
    assert relative["schedule"]["iso"] == "+90s"


def test_validate_job_spec_rejects_unknown_top_level_key() -> None:
    with pytest.raises(SchedulerError, match="unknown job key bogus"):
        validate_job_spec(
            {"id": "x", "kind": "spawn_agent", "schedule": {"type": "interval", "seconds": 5},
             "bogus": 1}
        )


# ---------------------------------------------------------------------------
# 2. Engine: add / list / duplicates / persistence
# ---------------------------------------------------------------------------
async def test_engine_add_and_list_roundtrip(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    await engine.add_job(
        {"id": "bbb", "kind": "run_script", "schedule": {"type": "interval", "seconds": 30},
         "argv": ["true"]},
        persist=False,
    )
    await engine.add_job(
        {"id": "aaa", "kind": "spawn_agent", "schedule": {"type": "at",
                                                          "iso": "2030-01-01T00:00:00+00:00"}},
        persist=False,
    )
    jobs = engine.list_jobs()
    assert [job["id"] for job in jobs] == ["aaa", "bbb"]  # sorted by id
    assert all(set(job) == {"id", "kind", "created_ts", "schedule"} for job in jobs)
    assert jobs[0]["schedule"] == {"type": "at"}  # schedule TYPE only, no raw trigger internals
    assert jobs[1]["schedule"] == {"type": "interval"}
    await engine.stop()


async def test_engine_add_dup_id_rejected(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None, jobs_file="state/other.json")
    spec = {"id": "dup", "kind": "run_script", "schedule": {"type": "interval", "seconds": 1},
            "argv": ["true"]}
    await engine.add_job(spec, persist=False)
    with pytest.raises(SchedulerError, match="job id already exists"):
        await engine.add_job(spec, persist=False)
    await engine.stop()


async def test_engine_persist_and_restore_roundtrip(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    created = await engine.add_job(
        {"id": "tick", "kind": "run_script", "schedule": {"type": "interval", "seconds": 60},
         "argv": ["true"]}
    )
    assert set(created) == {"id", "kind", "created_ts"}
    await engine.stop()
    on_disk = json.loads(_jobs_file(cfg).read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert [job["id"] for job in on_disk["jobs"]] == ["tick"]

    fresh = SchedulerEngine(cfg, bus=None)
    result = await fresh.restore()
    assert result == {"restored": 1, "misfire_skipped": 0}
    listed = fresh.list_jobs()
    assert len(listed) == 1
    assert listed[0]["id"] == "tick" and listed[0]["created_ts"] == created["created_ts"]
    await fresh.stop()


# ---------------------------------------------------------------------------
# 3. Restore misfire policy (FakeClock-driven boot decisions)
# ---------------------------------------------------------------------------
async def test_restore_misfire_skip_past_at_job(tmp_volume, cfg) -> None:
    boot = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
    clock = _FakeClock(boot)
    path = _jobs_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "late",
                        "kind": "spawn_agent",
                        "created_ts": boot.isoformat(),
                        "schedule": {"type": "at", "iso": (boot - timedelta(hours=1)).isoformat()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = SchedulerEngine(cfg, bus=None, clock=clock)
    result = await engine.restore()
    assert result == {"restored": 0, "misfire_skipped": 1}
    assert engine.list_jobs() == []  # dropped from the registry
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1, "jobs": []}
    rows = read_jsonl(_ledger_file(cfg))
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "misfire_skip"
    assert (row["job_id"], row["job_kind"]) == ("late", "spawn_agent")
    assert row["ts"] == boot.isoformat()  # ledger ts comes from the engine clock


async def test_restore_within_grace_registers(tmp_volume, cfg) -> None:
    boot = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
    clock = _FakeClock(boot)
    path = _jobs_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "warm",
                        "kind": "run_script",
                        "argv": ["true"],
                        "schedule": {"type": "at", "iso": (boot - timedelta(seconds=10)).isoformat()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = SchedulerEngine(cfg, bus=None, clock=clock)
    result = await engine.restore()
    assert result == {"restored": 1, "misfire_skipped": 0}
    assert [job["id"] for job in engine.list_jobs()] == ["warm"]
    assert read_jsonl(_ledger_file(cfg)) == []  # no misfire row was appended
    await engine.stop()


# ---------------------------------------------------------------------------
# 4. Deterministic fires: ledger + sink + bus contract
# ---------------------------------------------------------------------------
async def test_ledger_row_written_on_deterministic_fire(tmp_volume, cfg) -> None:
    bus = Bus()
    queue = bus.subscribe("sched-test")
    sink_rows: list[dict] = []

    async def sink(row: dict) -> None:
        sink_rows.append(dict(row))

    calls: list[tuple[str, dict]] = []

    async def spawn(record: dict, payload: dict) -> None:
        calls.append((record.get("id"), payload))

    engine = SchedulerEngine(cfg, bus, sink=sink, spawn=spawn)
    now = datetime.now(UTC)
    await engine.add_job(
        {"id": "late-fire", "kind": "spawn_agent",
         "schedule": {"type": "at", "iso": (now - timedelta(seconds=10)).isoformat()}}
    )
    await engine.start()  # past-within-grace date trigger fires immediately (probe23)
    assert await _spin_until(lambda: bool(calls) and bool(sink_rows)), "job never fired"
    await engine.stop()

    rows = read_jsonl(_ledger_file(cfg))
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "fired"
    assert (row["job_id"], row["job_kind"]) == ("late-fire", "spawn_agent")
    assert set(row) == {"ts", "job_id", "job_kind", "outcome", "detail"}
    assert sink_rows[0] == row  # the sink receives EXACTLY the ledger dict
    topic, payload = await asyncio.wait_for(queue.get(), timeout=5)
    assert topic == "schedule.fired"
    assert payload == {"job_id": "late-fire", "job_kind": "spawn_agent", "outcome": "fired"}


async def test_error_outcome_recorded(tmp_volume, cfg) -> None:
    bus = Bus()
    queue = bus.subscribe("sched-test")
    sink_rows: list[dict] = []

    async def sink(row: dict) -> None:
        sink_rows.append(dict(row))

    async def boom(record: dict, payload: dict) -> None:
        raise RuntimeError("boom")

    engine = SchedulerEngine(cfg, bus, sink=sink, spawn=boom)
    now = datetime.now(UTC)
    await engine.add_job(
        {"id": "bad", "kind": "spawn_agent",
         "schedule": {"type": "at", "iso": (now - timedelta(seconds=10)).isoformat()}},
        persist=False,
    )
    await engine.start()
    assert await _spin_until(lambda: bool(sink_rows)), "job never fired"
    await engine.stop()

    rows = read_jsonl(_ledger_file(cfg))
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["detail"] == {"error": "boom"}
    assert [job["id"] for job in engine.list_jobs()] == ["bad"]  # the engine survived
    topic, payload = await asyncio.wait_for(queue.get(), timeout=5)  # bus still published
    assert topic == "schedule.fired" and payload["outcome"] == "error"


# ---------------------------------------------------------------------------
# 5. Removal + drop_task
# ---------------------------------------------------------------------------
async def test_remove_job_persists(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    for job_id in ("one", "two"):
        await engine.add_job(
            {"id": job_id, "kind": "run_script", "schedule": {"type": "interval", "seconds": 5},
             "argv": ["true"]}
        )
    await engine.remove_job("one")
    on_disk = json.loads(_jobs_file(cfg).read_text(encoding="utf-8"))
    assert [job["id"] for job in on_disk["jobs"]] == ["two"]
    assert [job["id"] for job in engine.list_jobs()] == ["two"]
    await engine.stop()


async def test_remove_unknown_raises_keyerror(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    with pytest.raises(KeyError):
        await engine.remove_job("nonesuch")


async def test_drop_task_creates_inbox_task(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    now = datetime.now(UTC)
    await engine.add_job(
        {"id": "drop1", "kind": "drop_task",
         "schedule": {"type": "at", "iso": (now - timedelta(seconds=10)).isoformat()},
         "description": "scheduled follow-up"},
        persist=False,
    )
    await engine.start()
    store = TaskStore(cfg)
    assert await _spin_until(lambda: bool(store.list_inbox())), "inbox task never appeared"
    await engine.stop()

    rows = read_jsonl(_ledger_file(cfg))
    assert len(rows) == 1 and rows[0]["outcome"] == "fired" and rows[0]["job_kind"] == "drop_task"
    task_id = rows[0]["detail"]["task_id"]
    assert store.list_inbox() == [task_id]
    raw = yaml.safe_load((store.inbox / task_id / "task.yaml").read_text(encoding="utf-8"))
    assert raw["description"] == "scheduled follow-up" and raw["origin"] == "scheduler"

    # Pickup simulation: the born state.json carries origin=scheduler.
    destination = store.move_from_inbox(task_id)
    TaskDir.birth(destination, cfg, origin="scheduler")
    state = store.read_state(task_id)
    assert state is not None and state["origin"] == "scheduler"
