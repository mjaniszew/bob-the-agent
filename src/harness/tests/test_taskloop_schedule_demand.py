"""Tests for the WP4 SUBTASK_DROP ``schedule:`` demand seam: the TaskLoop
schedule pop + scheduler_sink invocations (note/accepted event contract), the
supervisor late-bound sink-holder wiring (source inspection), the engine
``submit_demand`` translation (job-id grammar) and the fake-clock e2e path
drop-demand -> derived drop_task job -> scheduler inbox child.
Fixture patterns mirror tests/test_taskloop.py + tests/test_scheduler.py
(local copies; no cross-test imports)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import apscheduler.executors.base as _sched_ex_ns
import apscheduler.schedulers.base as _sched_base_ns
import apscheduler.triggers.cron as _sched_cron_ns
import apscheduler.triggers.date as _sched_date_ns
import apscheduler.triggers.interval as _sched_interval_ns
import pytest
import yaml

from harness.blocks import parse_blocks
from harness.bus import Bus
from harness.persist import read_jsonl
from harness.scheduler import SchedulerEngine, SchedulerError
from harness.session_manager import SessionManager
from harness.taskloop import TaskLoop
from harness.tasks import TaskDir, TaskStore

_P1 = "ADA23456"


class _FakeClock:
    """FakeClock-equivalent (engine `clock=` injection shape): now(tz=None) /
    advance(seconds) over a controllable aware start datetime."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self, tz: Any | None = None) -> datetime:
        return self._now if tz is None else self._now.astimezone(tz)


class _FakeDT(datetime):
    """datetime subclass injected into the 4 apscheduler namespaces: now(tz)
    returns the fake clock (the R11 scratch-probe FakeDT recipe)."""

    _clock: _FakeClock | None = None

    @classmethod
    def now(cls, tz: Any | None = None) -> datetime:
        if cls._clock is None:
            return datetime.now(tz)
        return cls._clock.now(tz)


def _sched_ns() -> tuple[Any, Any, Any, Any, Any]:
    # 5 fake-clock namespaces — mirrors the battery's _b_patch recipe: the
    # executor-side misfire check lives in apscheduler.executors.base, so it
    # MUST be patched too or the real wall clock rejects fake-clock jobs
    # (wall-time-dependent e2e failure found in orchestrator verification).
    return _sched_base_ns, _sched_date_ns, _sched_interval_ns, _sched_cron_ns, _sched_ex_ns


def _make_parent(store: TaskStore, cfg: Any, task_id: str, description: str) -> TaskDir:
    """A born (picked-up) parent task, the _handle_drop subject."""
    store.create_inbox_task(description, origin="user", task_id=task_id)
    destination = store.move_from_inbox(task_id)
    return TaskDir.birth(destination, cfg)


def _block(text: str) -> Any:
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    return blocks[0]


def _drop_text(description: str, schedule_yaml: str = "") -> str:
    return "```SUBTASK_DROP\n" + f"description: {description}\n" + schedule_yaml + "```"


def _events(store: Any, task_id: str) -> list[dict]:
    return read_jsonl(store.root / task_id / "events.jsonl")


def _kinds(store: Any, task_id: str) -> list[str]:
    return [event["kind"] for event in _events(store, task_id)]


def _child_yaml(store: Any, child_id: str) -> dict:
    return yaml.safe_load((store.inbox / child_id / "task.yaml").read_text(encoding="utf-8"))


async def _spin_until(predicate: Any, attempts: int = 100, tick: float = 0.05) -> bool:
    """Poll a condition without sleeps-based timing assumptions (5s budget)."""
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(tick)
    return predicate()


# ---------------------------------------------------------------------------
# 1. Demand accepted: the recording sink is invoked with the full payload and
#    BOTH the existing subtask_drop event and subtask_schedule_accepted land.
# ---------------------------------------------------------------------------
async def test_drop_with_schedule_demand_invokes_sink(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "demand parent chore")
    iso = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    # Quoted: YAML would otherwise auto-convert the RFC3339 literal into a
    # datetime object, which the closed events registry cannot JSON-dump.
    block = _block(
        _drop_text("scheduled child chore", f'schedule:\n  type: at\n  iso: "{iso}"\n')
    )
    seen: list[dict] = []

    async def scheduled_sink(payload: dict) -> None:
        seen.append(dict(payload))

    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=scheduled_sink)
    await loop._handle_drop(parent, block)

    child_id = store.list_inbox()[0]
    assert len(seen) == 1
    assert seen[0]["task_id"] == child_id
    assert seen[0]["schedule"] == {"type": "at", "iso": iso}
    assert seen[0]["description"] == "scheduled child chore"
    assert seen[0]["origin_task"] == _P1
    kinds = _kinds(store, _P1)
    assert "subtask_drop" in kinds and "subtask_schedule_accepted" in kinds
    accepted = [event for event in _events(store, _P1)
                if event["kind"] == "subtask_schedule_accepted"]
    assert accepted[0]["data"] == {"task_id": child_id, "schedule": {"type": "at", "iso": iso}}
    raw = _child_yaml(store, child_id)
    assert raw["origin"] == "agent" and raw["parent"] == _P1


# ---------------------------------------------------------------------------
# 2. Plain drop, no schedule key: the sink is NOT invoked, no schedule events.
# ---------------------------------------------------------------------------
async def test_drop_without_schedule_demand_no_sink_call(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "plain parent chore")
    block = _block(_drop_text("plain child chore"))
    seen: list[dict] = []

    async def scheduled_sink(payload: dict) -> None:
        seen.append(dict(payload))

    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=scheduled_sink)
    await loop._handle_drop(parent, block)

    assert len(store.list_inbox()) == 1
    assert not seen
    kinds = _kinds(store, _P1)
    assert "subtask_drop" in kinds
    assert "subtask_schedule_accepted" not in kinds and "subtask_schedule_note" not in kinds


# ---------------------------------------------------------------------------
# 3. Demand with NO sink wired: an honest note, the child is still created.
# ---------------------------------------------------------------------------
async def test_drop_with_schedule_demand_no_sink_records_note(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "unwired parent chore")
    schedule = {"type": "interval", "seconds": 30}
    block = _block(
        _drop_text("unwired child chore", "schedule:\n  type: interval\n  seconds: 30\n")
    )
    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=None)
    await loop._handle_drop(parent, block)

    child_id = store.list_inbox()[0]
    notes = [event for event in _events(store, _P1) if event["kind"] == "subtask_schedule_note"]
    assert len(notes) == 1
    assert notes[0]["data"] == {
        "reason": "schedule demand recorded but no scheduler sink wired",
        "schedule": schedule,
    }
    assert "subtask_drop" in _kinds(store, _P1)  # the drop itself stays accepted
    raw = _child_yaml(store, child_id)
    assert raw["origin"] == "agent"


# ---------------------------------------------------------------------------
# 4. A broken sink: note with the error, the drop itself stays accepted.
# ---------------------------------------------------------------------------
async def test_drop_with_schedule_sink_failure_records_note(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "doomed parent chore")
    block = _block(
        _drop_text("doomed child chore", "schedule:\n  type: interval\n  seconds: 5\n")
    )

    async def broken_sink(payload: dict) -> None:
        raise RuntimeError("sink boom")

    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=broken_sink)
    await loop._handle_drop(parent, block)

    child_id = store.list_inbox()[0]
    notes = [event for event in _events(store, _P1) if event["kind"] == "subtask_schedule_note"]
    assert len(notes) == 1
    assert notes[0]["data"]["reason"] == "scheduler sink failed: sink boom"
    assert "subtask_drop" in _kinds(store, _P1)
    accepted = [event for event in _events(store, _P1)
                if event["kind"] == "subtask_schedule_accepted"]
    assert accepted == []
    raw = _child_yaml(store, child_id)
    assert raw["origin"] == "agent"


# ---------------------------------------------------------------------------
# 5. Malformed schedule values (string / empty mapping) refuse the drop.
# ---------------------------------------------------------------------------
async def test_drop_schedule_demand_bad_mapping_refused(cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "picky parent chore")
    seen: list[dict] = []

    async def scheduled_sink(payload: dict) -> None:
        seen.append(dict(payload))

    loop = TaskLoop(cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=scheduled_sink)
    for body in ("schedule: not-a-map\n", "schedule: {}\n"):
        await loop._handle_drop(parent, _block(_drop_text("picky child chore", body)))
        refused = [
            event for event in _events(store, _P1) if event["kind"] == "subtask_refused"
        ]
        assert refused[-1]["data"]["reason"] == (
            "field 'schedule' must be a non-empty mapping"
        )
    assert store.list_inbox() == []
    assert not seen
    assert "subtask_drop" not in _kinds(store, _P1)


# ---------------------------------------------------------------------------
# 6. A schedule dict the ENGINE rejects (via the wired engine sink) does NOT
#    refuse the drop: accepted + a note carrying the scheduler error.
# ---------------------------------------------------------------------------
async def test_drop_schedule_demand_engine_rejects_records_note(tmp_volume, cfg) -> None:
    bus = Bus()
    store = TaskStore(cfg, bus=bus)
    parent = _make_parent(store, cfg, _P1, "validating parent chore")
    block = _block(
        _drop_text("crontab child chore", "schedule:\n  type: cron\n  expr: only three\n")
    )
    engine = SchedulerEngine(cfg, bus=None)
    loop = TaskLoop(
        cfg, SessionManager(cfg, bus=bus), bus=bus, scheduler_sink=engine.submit_demand
    )
    await loop._handle_drop(parent, block)

    child_id = store.list_inbox()[0]
    notes = [event for event in _events(store, _P1) if event["kind"] == "subtask_schedule_note"]
    assert len(notes) == 1
    assert notes[0]["data"]["reason"] == (
        "scheduler sink failed: "
        "cron expr must have 6 fields (second minute hour day month day_of_week), got 2"
    )
    assert "subtask_drop" in _kinds(store, _P1)
    raw = _child_yaml(store, child_id)
    assert raw["origin"] == "agent"
    assert engine.list_jobs() == []  # the malformed demand never became a job
    with pytest.raises(SchedulerError):
        await engine.submit_demand({"description": "x", "schedule": {"type": "cron"}})


# ---------------------------------------------------------------------------
# 7. Source inspection: the supervisor carries the late-bound wiring.
# ---------------------------------------------------------------------------
def test_supervisor_sink_holder_wiring() -> None:
    import harness.supervisor

    text = Path(harness.supervisor.__file__).read_text(encoding="utf-8")
    assert "_scheduler_demand_sink" in text
    assert "scheduler_sink=" in text
    assert "sink_holder" in text
    assert "sink_holder[\"sink\"] = scheduler_.submit_demand" in text
    assert "sink_holder[\"sink\"] = None" in text


# ---------------------------------------------------------------------------
# 8. submit_demand: derived job id grammar + registry presence.
# ---------------------------------------------------------------------------
async def test_submit_demand_id_alphabet(tmp_volume, cfg) -> None:
    engine = SchedulerEngine(cfg, bus=None)
    out = await engine.submit_demand(
        {
            "task_id": "BBB23457",
            "origin_task": _P1,
            "description": "demand child chore",
            "schedule": {"type": "interval", "seconds": 30},
        }
    )
    job_id = out["id"]
    assert re.fullmatch(r"[A-Z2-7]{8}", job_id)
    assert job_id.startswith("SD")
    assert out == {"id": job_id, "kind": "drop_task", "created_ts": out["created_ts"]}
    listed = engine.list_jobs()
    assert [job["id"] for job in listed] == [job_id]
    assert listed[0]["kind"] == "drop_task"
    assert listed[0]["schedule"] == {"type": "interval"}
    await engine.stop()


# ---------------------------------------------------------------------------
# 9. Fake-clock e2e: drop demand -> derived drop_task job -> scheduler child.
# ---------------------------------------------------------------------------
async def test_demand_to_scheduled_task_e2e_fakeclock(tmp_volume, cfg, monkeypatch) -> None:
    boot = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
    clock = _FakeClock(boot)
    _FakeDT._clock = clock
    for namespace in _sched_ns():
        monkeypatch.setattr(namespace, "datetime", _FakeDT)

    engine = SchedulerEngine(cfg, bus=None, clock=clock)
    await engine.restore()
    await engine.start()

    store = TaskStore(cfg)
    parent = _make_parent(store, cfg, _P1, "clocked demand parent")
    block = _block(_drop_text("clocked demand child", "schedule:\n  type: at\n  iso: +5s\n"))
    loop = TaskLoop(cfg, SessionManager(cfg), scheduler_sink=engine.submit_demand)
    await loop._handle_drop(parent, block)

    kinds = _kinds(store, _P1)
    assert "subtask_schedule_accepted" in kinds and "subtask_drop" in kinds
    jobs = engine.list_jobs()
    assert len(jobs) == 1 and jobs[0]["id"].startswith("SD")
    assert jobs[0]["schedule"] == {"type": "at"}
    # _handle_drop itself already put the drop child into the inbox (origin
    # agent); the SCHEDULER child (origin=scheduler) appears only after fire.
    assert len(store.list_inbox()) == 1

    ledger = Path(cfg.storage.data_dir) / "state" / "scheduler_runs_ledger.jsonl"
    assert read_jsonl(ledger) == []  # not yet due
    clock.advance(6)  # past the +5s run_date resolved against the engine clock
    engine._sched.wakeup()
    assert await _spin_until(lambda: len(read_jsonl(ledger)) == 1), "job never fired"
    await engine.stop()

    rows = read_jsonl(ledger)
    assert rows[0]["outcome"] == "fired" and rows[0]["job_kind"] == "drop_task"
    assert rows[0]["detail"] == {"task_id": rows[0]["detail"]["task_id"]}
    assert rows[0]["ts"] == (boot + timedelta(seconds=6)).isoformat()  # engine clock at fire
    children = [entry for entry in store.list_inbox() if _child_yaml(store, entry)["origin"]
                == "scheduler"]
    assert len(children) == 1
    child_id = children[0]
    raw = _child_yaml(store, child_id)
    assert raw["description"] == "clocked demand child"
    assert rows[0]["detail"] == {"task_id": child_id}
    assert len(store.list_inbox()) == 2  # the drop child + the scheduler child
    drop_children = [entry for entry in store.list_inbox()
                     if _child_yaml(store, entry)["origin"] == "agent"]
    assert len(drop_children) == 1
    _FakeDT._clock = None
