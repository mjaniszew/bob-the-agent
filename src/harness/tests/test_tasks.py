"""Tests for harness.tasks (R9 WP1): task.yaml schema (D15), the task lifecycle
FSM (ARCHITECTURE.md 2.9.3), ready-check + F13/F14 guards and the D17
SUBTASK_DROP validation/depth-cap primitives. TEST_PLAN row 19, first column."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from harness.config import HarnessConfig
from harness.persist import read_jsonl
from harness.tasks import (
    EVENTS,
    STATE_KEYS,
    TaskDir,
    TaskExistsError,
    TaskFSMError,
    TaskSchemaError,
    TaskStore,
    detect_cycle,
    load_task_yaml,
    subtask_depth_cap,
    validate_depends,
    validate_subtask_drop,
    validate_task_yaml,
)

_STAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TS_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_ROOT_ID = "AAA23456"
_DEP_ID = "BBB23457"
_PEER_ID = "CCC24567"

_DONE_WALK = ("ANALYZING", "ANALYZED", "READY", "RUNNING", "DONE")


class _RecordingBus:
    """Minimal duck-typed bus: anything with ``async def publish(topic, payload)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> int:
        self.events.append((topic, dict(payload)))
        return 1


def _make_task(
    store: TaskStore, cfg: HarnessConfig, task_id: str, *, origin: str = "user", **fields: object
) -> TaskDir:
    """create -> move -> birth: a picked-up task at the born ``appeared`` state."""
    store.create_inbox_task("do the thing", origin=origin, task_id=task_id, **fields)
    destination = store.move_from_inbox(task_id)
    return TaskDir.birth(destination, cfg, origin=origin, bus=store.bus())


async def _drive(task: TaskDir, states: Sequence[str]) -> None:
    for state in states:
        await task.transition(state)


def _events_path(store: TaskStore, task_id: str) -> Path:
    return store.root / task_id / "events.jsonl"


# ---------------------------------------------------------------------------
# 1. Schema happy (TEST_PLAN row 19: legal schema)
# ---------------------------------------------------------------------------
def test_validate_minimal_description_only() -> None:
    normalized = validate_task_yaml({"description": "  Fix failing tests.  "})
    assert normalized == {
        "description": "  Fix failing tests.  ",
        "agent": None,
        "workdir": None,
        "depends_on": [],
        "max_depth": None,
        "oneway": False,
        "parent": None,
        "origin": None,
    }


def test_validate_full_fields_normalize(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    normalized = validate_task_yaml(
        {
            "description": "Build the site",
            "agent": "opencode",
            "workdir": str(site),
            "depends_on": [_DEP_ID, _DEP_ID],
            "max_depth": 1,
            "oneway": True,
            "parent": _ROOT_ID,
            "origin": "agent",
        },
        workdir_prefix=str(tmp_path),
    )
    assert normalized == {
        "description": "Build the site",
        "agent": "opencode",
        "workdir": str(site),
        "depends_on": [_DEP_ID],
        "max_depth": 1,
        "oneway": True,
        "parent": _ROOT_ID,
        "origin": "agent",
    }


def test_validate_workdir_existing_dir_required(tmp_path: Path) -> None:
    site = tmp_path / "site"
    with pytest.raises(TaskSchemaError, match="does not exist"):
        validate_task_yaml(
            {"description": "x", "workdir": str(site)}, workdir_prefix=str(tmp_path)
        )
    site.mkdir()
    assert validate_task_yaml(
        {"description": "x", "workdir": str(site)}, workdir_prefix=str(tmp_path)
    )["workdir"] == str(site)


def test_validate_workdir_prefix_is_by_default_project_root() -> None:
    with pytest.raises(TaskSchemaError, match="must start with"):
        validate_task_yaml({"description": "x", "workdir": "/etc/tmp"})
    with pytest.raises(TaskSchemaError, match="must start with"):
        validate_task_yaml({"description": "x", "workdir": "/app/projectsfoo"})


# ---------------------------------------------------------------------------
# 2. Schema sad (TEST_PLAN row 19: unknown keys rejected, caps, bad values)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"description": "x", "assignee": "bob"}, "unknown key"),
        ({"description": "x", "due": "tomorrow", "priority": 1}, "unknown key"),
        ({"id": _ROOT_ID, "description": "x"}, "must not carry an id field"),
        ({"description": "123456789"}, "9 chars"),
        ({"description": ""}, "non-empty"),
        ({"description": "   "}, "non-empty"),
        ({"description": 123}, "must be a string"),
        ({"description": "x", "depends_on": _DEP_ID}, "must be a list"),
        ({"description": "x", "depends_on": ["TOOLONG09"]}, "bad entry 'TOOLONG09'"),
        ({"description": "x", "depends_on": ["abc23456"]}, "bad entry 'abc23456'"),
        ({"description": "x", "depends_on": [123]}, "bad entry 123"),
        ({"description": "x", "agent": "claude"}, "'agent' must be one of"),
        ({"description": "x", "origin": "cron"}, "'origin' must be one of"),
        ({"description": "x", "max_depth": -1}, "max_depth"),
        ({"description": "x", "max_depth": "2"}, "max_depth"),
        ({"description": "x", "max_depth": True}, "max_depth"),
        ({"description": "x", "oneway": 1}, "boolean"),
        ({"description": "x", "parent": "short"}, "parent"),
        ({}, "missing required field 'description'"),
    ],
)
def test_validate_sad(payload: object, match: str) -> None:
    with pytest.raises(TaskSchemaError, match=match):
        validate_task_yaml(payload, max_desc_chars=8)


def test_validate_description_literal_over_4000() -> None:
    with pytest.raises(TaskSchemaError, match="4001 chars"):
        validate_task_yaml({"description": "x" * 4001})


def test_load_task_yaml_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(TaskSchemaError, match="must be a YAML mapping, got str"):
        load_task_yaml(path)


def test_load_task_yaml_bad_flow_yaml(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text("{description: [unclosed\n", encoding="utf-8")
    with pytest.raises(TaskSchemaError, match="invalid YAML"):
        load_task_yaml(path)


def test_load_task_yaml_missing(tmp_path: Path) -> None:
    with pytest.raises(TaskSchemaError, match="not found"):
        load_task_yaml(tmp_path / "gone" / "task.yaml")


def test_load_task_yaml_happy(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text("description: Fix it\norigin: user\n", encoding="utf-8")
    loaded = load_task_yaml(path, max_desc_chars=100)
    assert loaded["description"] == "Fix it" and loaded["origin"] == "user"


# ---------------------------------------------------------------------------
# 3. FSM legal transitions (TEST_PLAN row 19: legal transition walk)
# ---------------------------------------------------------------------------
async def test_fsm_happy_walk_writes_state_and_events(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    assert task.state == "appeared"
    for new in _DONE_WALK:
        before = store.read_state(_ROOT_ID)
        await task.transition(new)
        data = store.read_state(_ROOT_ID)
        assert data is not None and before is not None
        assert data["state"] == new
        assert list(data) == list(STATE_KEYS)
        assert data["id"] == _ROOT_ID
        assert data["attempt"] == 0 and data["agent_session"] is None
        assert data["parent"] is None and data["origin"] == "user" and data["depth"] == 0
        assert _STAMP_PATTERN.fullmatch(str(data["updated"]))
        assert data["updated"] >= before["updated"]
    events = read_jsonl(_events_path(store, _ROOT_ID))
    assert [event["kind"] for event in events] == ["state_change"] * 5
    assert [event["data"]["new"] for event in events] == list(_DONE_WALK)
    assert [event["data"]["old"] for event in events] == ["appeared", *_DONE_WALK[:-1]]
    assert set(events[0]) == {"ts", "task_id", "kind", "data"}
    assert events[0]["task_id"] == _ROOT_ID


async def test_fsm_parking_and_gating_paths(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    parked = _make_task(store, cfg, _ROOT_ID)
    await _drive(parked, ("ANALYZING", "ANALYZED", "READY"))
    await parked.transition("WAITING_DEPS")
    assert parked.state == "WAITING_DEPS" and store.read_state(_ROOT_ID)["state"] == "WAITING_DEPS"
    await parked.transition("READY")
    await parked.transition("RUNNING")
    await parked.transition("WAITING_INPUT")
    await parked.append_event("gate_opened", {"question": "which env?"})
    await parked.transition("RUNNING")
    await parked.append_event("gate_answered", {"answer": "staging"})
    await parked.transition("DONE")
    kinds = [event["kind"] for event in read_jsonl(_events_path(store, _ROOT_ID))]
    assert "gate_opened" in kinds and "gate_answered" in kinds


async def test_fsm_failed_reasons_recorded(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    timed_out = _make_task(store, cfg, _ROOT_ID)
    await _drive(timed_out, ("ANALYZING", "ANALYZED", "READY", "RUNNING", "WAITING_INPUT"))
    await timed_out.transition("FAILED", reason="gate_timeout")
    exhausted = _make_task(store, cfg, _DEP_ID)
    await _drive(exhausted, ("ANALYZING", "ANALYZED", "READY", "RUNNING"))
    await exhausted.transition("FAILED", reason="attempt exhausted")
    events = read_jsonl(_events_path(store, _DEP_ID))
    assert events[-1]["data"] == {"old": "RUNNING", "new": "FAILED", "reason": "attempt exhausted"}
    assert store.read_state(_DEP_ID)["state"] == "FAILED"
    syntax = _make_task(store, cfg, _PEER_ID)
    await syntax.transition("ANALYZING")
    await syntax.transition("FAILED", reason="invalid task.yaml")
    assert store.read_state(_PEER_ID)["state"] == "FAILED"


async def test_fsm_publishes_task_state_on_bus(cfg: HarnessConfig) -> None:
    bus = _RecordingBus()
    store = TaskStore(cfg, bus=bus)
    task = _make_task(store, cfg, _ROOT_ID)
    await task.transition("ANALYZING", reason="picked up")
    await task.transition("ANALYZED")
    assert bus.events == [
        (
            "task.state",
            {"task_id": _ROOT_ID, "old": "appeared", "new": "ANALYZING", "reason": "picked up"},
        ),
        ("task.state", {"task_id": _ROOT_ID, "old": "ANALYZING", "new": "ANALYZED"}),
    ]


# ---------------------------------------------------------------------------
# 4. FSM illegal transitions (TEST_PLAN row 19: illegal moves rejected)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("path_states", "new_state", "match"),
    [
        ((), "RUNNING", "appeared -> RUNNING"),
        ((), "BOGUS", "appeared -> BOGUS"),
        (("ANALYZING", "ANALYZED"), "RUNNING", "ANALYZED -> RUNNING"),
        ((), "ANALYZED", "appeared -> ANALYZED"),
        (
            ("ANALYZING", "ANALYZED", "READY", "WAITING_DEPS"),
            "DONE",
            "WAITING_DEPS -> DONE",
        ),
        (_DONE_WALK, "READY", "DONE -> READY"),
        (("ANALYZING", "FAILED"), "RUNNING", "FAILED -> RUNNING"),
    ],
)
async def test_fsm_illegal_transitions_leave_files_untouched(
    cfg: HarnessConfig, path_states: tuple[str, ...], new_state: str, match: str
) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    await _drive(task, path_states)
    snapshot = store.read_state(_ROOT_ID)
    events_before = read_jsonl(_events_path(store, _ROOT_ID))
    with pytest.raises(TaskFSMError, match=match):
        await task.transition(new_state)
    assert store.read_state(_ROOT_ID) == snapshot
    assert read_jsonl(_events_path(store, _ROOT_ID)) == events_before


async def test_fsm_unknown_current_state_rejected(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    _make_task(store, cfg, _ROOT_ID)
    (store.root / _ROOT_ID / "state.json").write_text(
        json.dumps({"id": _ROOT_ID, "state": "done"}), encoding="utf-8"
    )
    task = store.open_task(_ROOT_ID)
    with pytest.raises(TaskFSMError, match="illegal transition done -> READY"):
        await task.transition("READY")


# ---------------------------------------------------------------------------
# 5. Ready-check + dependency/cycle guards (F14)
# ---------------------------------------------------------------------------
async def test_ready_check_true_on_done_dep(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    dep = _make_task(store, cfg, _DEP_ID)
    await _drive(dep, _DONE_WALK)
    _make_task(store, cfg, _ROOT_ID, depends_on=[_DEP_ID])
    assert store.ready_check(_ROOT_ID) is True


async def test_ready_check_false_on_failed_dep(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    dep = _make_task(store, cfg, _PEER_ID)
    await dep.transition("ANALYZING")
    await dep.transition("FAILED", reason="syntax")
    _make_task(store, cfg, _ROOT_ID, depends_on=[_PEER_ID])
    assert store.ready_check(_ROOT_ID) is False


async def test_ready_check_false_on_inbox_only_dep(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("pending dep", origin="user", task_id=_DEP_ID)
    _make_task(store, cfg, _ROOT_ID, depends_on=[_DEP_ID])
    assert store.list_tasks() == {_ROOT_ID: "appeared"}
    assert store.list_inbox() == [_DEP_ID]
    assert store.ready_check(_ROOT_ID) is False


async def test_ready_check_unknown_dep_raises_naming_id(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    _make_task(store, cfg, _ROOT_ID, depends_on=[_DEP_ID])
    with pytest.raises(TaskSchemaError, match=f"unknown depends_on id '{_DEP_ID}'"):
        store.ready_check(_ROOT_ID)


async def test_cycle_and_self_dependency_rejected(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    _make_task(store, cfg, _ROOT_ID, depends_on=[_DEP_ID])
    _make_task(store, cfg, _DEP_ID, depends_on=[_ROOT_ID])
    with pytest.raises(TaskSchemaError, match="dependency cycle detected:"):
        store.check_cycles()
    with pytest.raises(TaskSchemaError, match="dependency cycle detected"):
        store.ready_check(_ROOT_ID)
    with pytest.raises(TaskSchemaError, match="dependency cycle detected: AAA23456 -> AAA23456"):
        detect_cycle([_ROOT_ID], {_ROOT_ID: [_ROOT_ID]})


def test_parent_cycle_rejected(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    _make_task(store, cfg, _ROOT_ID, origin="agent", parent=_DEP_ID)
    _make_task(store, cfg, _DEP_ID, origin="agent", parent=_ROOT_ID)
    with pytest.raises(TaskSchemaError, match="dependency cycle detected"):
        store.check_cycles()


def test_validate_depends_format_and_existence() -> None:
    validate_depends([], {})
    validate_depends([_DEP_ID], {_DEP_ID: "DONE"})
    with pytest.raises(TaskSchemaError, match="invalid depends_on task id 'short'"):
        validate_depends(["short"], {})
    with pytest.raises(TaskSchemaError, match="unknown depends_on id 'ZZZ24567'"):
        validate_depends(["ZZZ24567"], {_ROOT_ID: "READY"})


# ---------------------------------------------------------------------------
# 6. Pickup move primitive (F13 dedupe by id)
# ---------------------------------------------------------------------------
def test_move_from_inbox_complete_and_atomic(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("move me", origin="user", task_id=_ROOT_ID)
    inbox_dir = store.inbox / _ROOT_ID
    TaskDir.birth(inbox_dir, cfg)
    yaml_before = (inbox_dir / "task.yaml").read_text(encoding="utf-8")
    state_before = json.loads((inbox_dir / "state.json").read_text(encoding="utf-8"))
    destination = store.move_from_inbox(_ROOT_ID)
    assert destination == store.root / _ROOT_ID
    assert (destination / "task.yaml").read_text(encoding="utf-8") == yaml_before
    assert json.loads((destination / "state.json").read_text(encoding="utf-8")) == state_before
    assert store.read_state(_ROOT_ID) == state_before
    assert not inbox_dir.exists()
    assert store.list_inbox() == []


def test_move_from_inbox_second_move_raises(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("first", origin="user", task_id=_ROOT_ID)
    store.move_from_inbox(_ROOT_ID)
    (store.inbox / _ROOT_ID).mkdir()
    with pytest.raises(TaskExistsError, match="F13 dedupe"):
        store.move_from_inbox(_ROOT_ID)


def test_move_from_inbox_unknown_id_raises(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    with pytest.raises(TaskSchemaError, match="no inbox task"):
        store.move_from_inbox(_DEP_ID)


def test_move_and_create_reject_bad_ids(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    with pytest.raises(ValueError, match="expected 8 chars"):
        store.move_from_inbox("../evil")
    with pytest.raises(ValueError, match="expected 8 chars"):
        store.create_inbox_task("x", origin="user", task_id="shorty")


# ---------------------------------------------------------------------------
# 7. events.jsonl (append-only audit; closed registry)
# ---------------------------------------------------------------------------
async def test_events_append_and_read_back(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    await task.append_event("note", {"hello": "world"})
    await task.append_event("dep_wait", {"on": [_DEP_ID]})
    await task.append_event("subtask_drop", {"child": _PEER_ID})
    events = read_jsonl(_events_path(store, _ROOT_ID))
    assert [event["kind"] for event in events] == ["note", "dep_wait", "subtask_drop"]
    for event in events:
        assert set(event) == {"ts", "task_id", "kind", "data"}
        assert event["task_id"] == _ROOT_ID
        assert _STAMP_PATTERN.fullmatch(event["ts"])
    assert events[1]["data"] == {"on": [_DEP_ID]}


async def test_events_unknown_kind_raises(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    with pytest.raises(ValueError, match="unknown task event kind 'memo'"):
        await task.append_event("memo", {"oops": True})
    assert read_jsonl(_events_path(store, _ROOT_ID)) == []


def test_events_registry_matches_design() -> None:
    assert EVENTS == {
        "appeared",
        "state_change",
        "gate_opened",
        "gate_answered",
        "note",
        "subtask_drop",
        "subtask_refused",
        "subtask_schedule_note",
        "subtask_schedule_accepted",
        "dep_wait",
        "result_written",
    }


# ---------------------------------------------------------------------------
# 8. create_inbox_task (the only inbox writer)
# ---------------------------------------------------------------------------
def test_create_inbox_task_round_trip_and_id_format(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task_id = store.create_inbox_task(
        "Fix the failing pytest in tests/test_payments.py", origin="user"
    )
    assert re.fullmatch(r"[A-Z2-7]{8}", task_id)
    assert not (store.inbox / task_id / "state.json").exists()
    payload = yaml.safe_load((store.inbox / task_id / "task.yaml").read_text(encoding="utf-8"))
    assert payload == {
        "description": "Fix the failing pytest in tests/test_payments.py",
        "origin": "user",
    }
    assert store.list_inbox() == [task_id]


def test_create_inbox_task_subtask_drop_fields(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("parent task", origin="user", task_id=_ROOT_ID)
    child = store.create_inbox_task(
        "child task",
        origin="agent",
        parent=_ROOT_ID,
        depends_on=[],
        max_depth=1,
        agent="hermes",
        oneway=True,
        task_id=_PEER_ID,
    )
    assert child == _PEER_ID
    payload = yaml.safe_load((store.inbox / _PEER_ID / "task.yaml").read_text(encoding="utf-8"))
    assert payload == {
        "description": "child task",
        "agent": "hermes",
        "max_depth": 1,
        "oneway": True,
        "parent": _ROOT_ID,
        "origin": "agent",
    }
    assert "depends_on" not in payload


def test_create_inbox_task_duplicate_and_bad_ids(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("taken", origin="user", task_id=_ROOT_ID)
    with pytest.raises(TaskExistsError, match="F13 dedupe"):
        store.create_inbox_task("clash", origin="user", task_id=_ROOT_ID)
    with pytest.raises(ValueError, match="expected 8 chars"):
        store.create_inbox_task("bad", origin="user", task_id="abc23456")


def test_create_inbox_task_description_cap(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    with pytest.raises(TaskSchemaError, match="4001 chars"):
        store.create_inbox_task("x" * 4001, origin="user")


def test_create_inbox_task_description_not_a_string(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    with pytest.raises(TaskSchemaError, match="must be a string"):
        store.create_inbox_task(123, origin="user")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 9. result.md (runner's DONE artifact)
# ---------------------------------------------------------------------------
def test_write_result_meta_and_event(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    assert not (store.root / _ROOT_ID / "result.md").exists()
    task.write_result("All checks green.\n", agent="hermes", duration_s=1.5)
    result = (store.root / _ROOT_ID / "result.md").read_text(encoding="utf-8")
    assert "All checks green." in result
    assert _ROOT_ID in result
    assert "- agent: hermes" in result
    assert "- duration_s: 1.5" in result
    assert _TS_PATTERN.search(result)
    events = read_jsonl(_events_path(store, _ROOT_ID))
    assert events[-1]["kind"] == "result_written"
    task.write_result("Second pass final.")
    result = (store.root / _ROOT_ID / "result.md").read_text(encoding="utf-8")
    assert "Second pass final." in result
    assert "- agent" not in result
    assert _TS_PATTERN.search(result)
    assert len(read_jsonl(_events_path(store, _ROOT_ID))) == 2


def test_write_result_absent_before_done(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    _make_task(store, cfg, _ROOT_ID)
    assert not (store.root / _ROOT_ID / "result.md").exists()


# ---------------------------------------------------------------------------
# 10. list_tasks / list_inbox across mixed dirs
# ---------------------------------------------------------------------------
async def test_list_tasks_across_mixed_dirs(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    analyzing = _make_task(store, cfg, "DDD23567")
    await analyzing.transition("ANALYZING")
    failed = _make_task(store, cfg, "EEE24567")
    await failed.transition("ANALYZING")
    await failed.transition("FAILED", reason="syntax")
    store.create_inbox_task("awaiting pickup", origin="user", task_id=_ROOT_ID)
    (store.root / "FFF24567").mkdir()
    (store.root / "not-a-task.txt").write_text("junk", encoding="utf-8")
    assert store.list_tasks() == {"DDD23567": "ANALYZING", "EEE24567": "FAILED"}
    assert store.list_inbox() == [_ROOT_ID]


def test_lists_empty_store(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    assert store.list_tasks() == {}
    assert store.list_inbox() == []


def test_read_state_and_open_task_edges(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    assert store.read_state(_ROOT_ID) is None
    with pytest.raises(ValueError, match="invalid task id"):
        store.read_state("short")
    with pytest.raises(TaskSchemaError, match="is missing"):
        store.open_task(_ROOT_ID)


# ---------------------------------------------------------------------------
# D17 extras: SUBTASK_DROP validation + depth-cap arithmetic
# ---------------------------------------------------------------------------
def test_subtask_drop_validation_happy() -> None:
    normalized = validate_subtask_drop(
        {"description": "Write the README", "agent": "opencode", "depends_on": [_DEP_ID]}
    )
    assert normalized == {
        "description": "Write the README",
        "agent": "opencode",
        "depends_on": [_DEP_ID],
    }
    assert validate_subtask_drop({"description": "minimal"}) == {
        "description": "minimal",
        "agent": None,
        "depends_on": [],
    }


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"description": "x", "assignee": "bob"}, "unknown key"),
        ({}, "missing required field 'description'"),
        ({"description": ""}, "non-empty"),
        ({"description": "x", "agent": "claude"}, "'agent' must be one of"),
        ({"description": "x", "depends_on": _ROOT_ID}, "must be a list"),
        ({"description": "x", "depends_on": ["TOOLONG09"]}, "bad entry 'TOOLONG09'"),
        ("not a mapping", "must be a YAML mapping"),
        ({"description": ["nope"]}, "must be a string"),
    ],
)
def test_subtask_drop_validation_sad(fields: object, match: str) -> None:
    with pytest.raises(TaskSchemaError, match=match):
        validate_subtask_drop(fields)


def test_subtask_drop_description_cap_parameterized() -> None:
    with pytest.raises(TaskSchemaError, match="41 chars"):
        validate_subtask_drop({"description": "x" * 41}, max_desc_chars=40)
    assert validate_subtask_drop({"description": "x" * 40}, max_desc_chars=40)["description"] == (
        "x" * 40
    )


def test_subtask_depth_cap_matrix() -> None:
    assert subtask_depth_cap(None, default_max_depth=2) == 2
    assert subtask_depth_cap(None, default_max_depth=0) == 1  # D17 floor 1
    assert subtask_depth_cap(2) == 2
    assert subtask_depth_cap(1) == 1
    assert subtask_depth_cap(0) == 0  # "at depth 0 the drop is refused"
    assert subtask_depth_cap(None, default_max_depth=2) - 1 == 1  # child written cap-1


# ---------------------------------------------------------------------------
# TaskDir.birth (the pickup half of state-birth, F17 idempotency) + attempts
# ---------------------------------------------------------------------------
def test_birth_validates_and_is_idempotent(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    store.create_inbox_task("x", origin="user", task_id=_ROOT_ID)
    destination = store.move_from_inbox(_ROOT_ID)
    with pytest.raises(TaskSchemaError, match="origin"):
        TaskDir.birth(destination, cfg, origin="cron")
    with pytest.raises(TaskSchemaError, match="parent"):
        TaskDir.birth(destination, cfg, parent="short")
    task = TaskDir.birth(destination, cfg)
    state_before = store.read_state(_ROOT_ID)
    born_again = TaskDir.birth(destination, cfg, depth=7)
    assert task.state == born_again.state == "appeared"
    assert store.read_state(_ROOT_ID) == state_before
    with pytest.raises(TaskSchemaError, match="does not exist"):
        TaskDir.birth(store.root / _PEER_ID, cfg)


def test_bump_attempt(cfg: HarnessConfig) -> None:
    store = TaskStore(cfg)
    task = _make_task(store, cfg, _ROOT_ID)
    assert task.bump_attempt() == 1
    assert task.bump_attempt() == 2
    assert store.read_state(_ROOT_ID)["attempt"] == 2
