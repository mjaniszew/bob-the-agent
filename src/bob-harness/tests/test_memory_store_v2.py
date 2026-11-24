"""P4 TDD failing-first: ``MemoryEngine`` reimplemented over harness ``FileMemoryStore``.

Plan ``docs/plans/10-pydantic-ai-v2-migration-plan.md`` Part 2 section
``P4 — Memory engine on FileMemoryStore (D4)``. One store shared with the
harness ``Memory`` capability; plain-file format + char budgets + audit /
digest byte-contracts preserved exactly. All store I/O goes through the
engine's private ``_persist_file`` (torn-tail safety via the store), replacing
``_atomic_write_text`` for the three canonical MEMORY.md/USER.md/lessons.md
files; fenced-edit parsing, audit rows and digest projections stay engine-side.

Verified 0.73.0 store contract (probe on FileMemoryStore): ``write`` needs the
current file's SHA-256 as ``expected_version=`` once the file exists (``None``
is only legal while missing; a stale version raises ``MemoryConflictError``);
identical-content writes over an existing file also conflict;
``replayed=True`` mutations come only from
``operation=MemoryOperation(id, fingerprint)`` receipts. The engine's persist
protocol therefore: read content, then its version, then write with the
observed version; test 3 additionally pins the receipt-replay surface
(``replayed=True``) that ``_persist_file`` passes through.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic_ai import UserError
from pydantic_ai_harness.memory import FileMemoryStore
from pydantic_ai.workspaces import LocalWorkspaceBackend

from bob_harness.blocks import parse_blocks
from bob_harness.config import load_config
from bob_harness.errors import MemoryBudgetError
from bob_harness.memory import MemoryEngine
from bob_harness.persist import read_jsonl


# ---------------------------------------------------------------------------
# helpers


def _store(tmp_volume: Path) -> FileMemoryStore:
    """A FileMemoryStore rooted at the engine's directory (no deprecation warning)."""
    (tmp_volume / "memory").mkdir(parents=True, exist_ok=True)  # store roots must exist
    return FileMemoryStore(
        ".", workspace=LocalWorkspaceBackend(tmp_volume / "memory")
    )


def _cfg():
    """Engine config on the tmp volume (same fixtures as test_memory.py)."""
    return load_config()


def _edit(yaml_body: str):
    """Parse one MEMORY_EDIT block out of its fenced representation."""
    return parse_blocks("```MEMORY_EDIT\n" + yaml_body + "\n```")[0]


def _seed_memory(tmp_volume: Path, name: str, text: str) -> Path:
    """Write a canonical memory file directly (bypassing both engine and store)."""
    path = tmp_volume / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _audit_lines(tmp_volume: Path) -> list[dict]:
    return read_jsonl(tmp_volume / "state" / "memory_audit.jsonl")


# ---------------------------------------------------------------------------
# 1-2: plain files / workspace requirement


def test_store_writes_plain_files_in_dir(tmp_volume: Path) -> None:
    """store.write('MEMORY.md', ...) creates <dir>/MEMORY.md and nothing else."""
    store = _store(tmp_volume)

    async def _go() -> None:
        await store.write("MEMORY.md", "hello\n", expected_version=None)

    asyncio.run(_go())
    listing = sorted(p.name for p in (tmp_volume / "memory").iterdir())
    assert listing == ["MEMORY.md"]  # no sidecars, no receipts journal
    assert (tmp_volume / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "hello\n"


def test_store_requires_workspace(tmp_volume: Path) -> None:
    """FileMemoryStore(dir) without workspace raises UserError on first read/write."""
    bare = FileMemoryStore(tmp_volume / "memory")
    with pytest.raises(UserError, match="no workspace"):
        asyncio.run(bare.read("MEMORY.md", max_chars=10))
    with pytest.raises(UserError, match="no workspace"):
        asyncio.run(bare.write("MEMORY.md", "x", expected_version=None))


# ---------------------------------------------------------------------------
# 3: mutation + version round-trip


def test_write_mutation_returns_version(tmp_volume: Path) -> None:
    """MemoryMutation(version=<sha256>, replayed, existed); an identical replay
    with the same operation receipt gives replayed=True; bytes round-trip."""
    from pydantic_ai_harness.memory import MemoryOperation

    store = _store(tmp_volume)
    sha_hello = hashlib.sha256(b"hello\n").hexdigest()
    op = MemoryOperation(id="op-1", fingerprint=sha_hello)

    async def _go() -> None:
        mut = await store.write(
            "MEMORY.md", "hello\n", expected_version=None, operation=op
        )
        assert mut.version == sha_hello
        assert (mut.replayed, mut.existed) == (False, False)
        replay = await store.write(
            "MEMORY.md", "hello\n", expected_version=None, operation=op
        )
        assert replay == mut.__class__(version=mut.version, replayed=True, existed=False)
        # plain (no receipt) write over EXISTING content: honest conflict on a
        # stale version; a current-version write reports existed=True
        with pytest.raises(Exception, match="changed before it could be written"):
            await store.write(
                "MEMORY.md", "x\n", expected_version=hashlib.sha256(b"older\n").hexdigest()
            )
        conflict = await store.write("MEMORY.md", "hello\n", expected_version=sha_hello)
        assert (conflict.replayed, conflict.existed) == (False, True)
        # version round-trip: on-disk bytes hash back to the reported version
        data = (tmp_volume / "memory" / "MEMORY.md").read_bytes()
        assert hashlib.sha256(data).hexdigest() == conflict.version

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# 4: budget check ordering (audit line BEFORE MemoryBudgetError)


def test_engine_budget_check_before_store_write(tmp_volume: Path) -> None:
    """Over-budget MEMORY_EDIT raises MemoryBudgetError AFTER the audit line."""
    _seed_memory(tmp_volume, "MEMORY.md", "a" * 2189 + "\n")  # 2190 chars
    engine = MemoryEngine(_cfg())
    file_path = tmp_volume / "memory" / "MEMORY.md"
    before = file_path.read_bytes()
    with pytest.raises(MemoryBudgetError) as excinfo:
        engine.apply_edit(_edit('op: add\nfile: MEMORY\ncontent: "123456789012345"'))
    assert str(excinfo.value) == "MEMORY.md budget 2200 exceeded: 2208 chars post-edit"
    assert file_path.read_bytes() == before  # store never saw the write
    audit = _audit_lines(tmp_volume)
    assert len(audit) == 1  # audit line landed BEFORE the raise
    assert audit[0]["ok"] is False and audit[0]["file"] == "MEMORY"
    assert audit[0]["before_len"] == 2190 and audit[0]["after_len"] == 2208
    assert "exceeded" in audit[0]["reason"]


# ---------------------------------------------------------------------------
# 5: fenced edit blocks over the store (same semantics as today)


def test_engine_fenced_edit_blocks(tmp_volume: Path) -> None:
    """add-bullet idempotent; replace/remove first-line-match via the store."""
    _seed_memory(tmp_volume, "MEMORY.md", "## Prefs\nstale v1 alpha\nstale v2 beta\n")
    engine = MemoryEngine(_cfg())
    # add (pure append through the store on an existing file)
    add = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: prefers dark mode"))
    assert add.ok and add.before_len == 38  # '## Prefs\n' + both stale lines
    assert add.after_len == add.before_len + len("- prefers dark mode\n")
    # idempotent re-apply: duplicate, file untouched
    after_first = (tmp_volume / "memory" / "MEMORY.md").read_bytes()
    dup = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: prefers dark mode"))
    assert dup.ok and dup.reason == "duplicate"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after_first
    # replace FIRST pattern match (multi-line content)
    result = engine.apply_edit(
        _edit("op: replace\nfile: MEMORY\npattern: stale\ncontent:\n  - fresh\n  - multi")
    )
    assert result.ok
    assert engine.load("MEMORY") == "## Prefs\nfresh\nmulti\nstale v2 beta\n- prefers dark mode\n"
    # remove FIRST pattern match (the only remaining 'stale' line)
    result = engine.apply_edit(_edit("op: remove\nfile: MEMORY\npattern: stale"))
    assert result.ok
    assert engine.load("MEMORY") == "## Prefs\nfresh\nmulti\n- prefers dark mode\n"
    # pattern not found is rejected, file untouched
    after = (tmp_volume / "memory" / "MEMORY.md").read_bytes()
    miss = engine.apply_edit(_edit("op: remove\nfile: MEMORY\npattern: absent"))
    assert miss.ok is False and miss.reason == "pattern not found"
    assert (tmp_volume / "memory" / "MEMORY.md").read_bytes() == after


# ---------------------------------------------------------------------------
# 6: harness Memory capability reads the SAME store the engine wrote


def test_memory_capability_reads_shared_store(tmp_volume: Path) -> None:
    """Memory(store=..., agent_name='executor') reads the file the engine wrote."""
    from pydantic_ai_harness.memory import Memory

    engine = MemoryEngine(_cfg())
    result = engine.apply_edit(_edit("op: add\nfile: MEMORY\ncontent: shared note"))
    assert result.ok
    file_path = tmp_volume / "memory" / "MEMORY.md"
    assert file_path.read_text(encoding="utf-8").endswith("- shared note\n")

    async def _go() -> None:
        store = _store(tmp_volume)
        cap = Memory(store=store, agent_name="executor")
        assert cap.store is store
        mfile = await cap.store.read("MEMORY.md", max_chars=10_000)
        assert mfile is not None
        assert mfile.content == file_path.read_text(encoding="utf-8")
        assert mfile.content.endswith("- shared note\n")
        # a capability-store write lands where the engine reads it back
        await cap.store.write(
            "MEMORY.md",
            file_path.read_text(encoding="utf-8") + "- from cap\n",
            expected_version=mfile.version,
        )
        assert engine.load("MEMORY").endswith("- from cap\n")

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# P4 impl step (2): executor assembly wires the SHARED store (default OFF)


def test_executor_memory_assembly_when_enabled(tmp_path: Path) -> None:
    """assemble_capabilities adds Memory(...) only when harness.memory.enabled.

    Tests default the toggle OFF (assembly stays capability-free); when ON,
    the capability shares the engine's <storage.data_dir>/memory root
    (D4: ONE store shared engine + capability).
    """
    from bob_harness.adapters.executor import assemble_capabilities

    (tmp_path / "executor.yaml").write_text(
        f"storage:\n  data_dir: '{tmp_path / 'data'}'\n"
        "harness:\n"
        "  memory:\n    enabled: true\n"
        "  skills:\n    enabled: false\n"
        "  ask_user:\n    enabled: false\n"
        "  subagents:\n    enabled: false\n"
        "  delegation:\n    enabled: false\n",
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path / "executor.yaml"))
    assert cfg.harness.memory.enabled is True
    caps = assemble_capabilities(cfg, workdir=str(tmp_path / "run-ws"))
    assert len(caps) == 1
    (cap,) = caps
    assert type(cap).__name__ == "Memory"
    assert (tmp_path / "data" / "memory").is_dir()  # shared store root exists

    # an engine edit lands where the capability's store reads it back
    engine = MemoryEngine(cfg)
    result = engine.apply_edit(_edit("op: add\nfile: USER\ncontent: assembly parity"))
    assert result.ok

    async def _go() -> None:
        mfile = await cap.store.read("USER.md", max_chars=10_000)
        assert mfile is not None
        assert mfile.content.endswith("- assembly parity\n")

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# 7: memory.updated event + audit rows unchanged


async def test_memory_updated_event_and_audit_rows(tmp_volume: Path) -> None:
    """memory.updated bus event + state/memory_audit.jsonl rows unchanged."""

    class FakeBus:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        async def publish(self, topic: str, payload: dict) -> int:
            self.events.append((topic, dict(payload)))
            return 1

    bus = FakeBus()
    engine = MemoryEngine(_cfg(), bus=bus)
    add = _edit("op: add\nfile: MEMORY\ncontent: event note")
    engine.apply_edit(add)
    dup = engine.apply_edit(add)  # duplicate: audit row, no event
    assert dup.reason == "duplicate"
    await asyncio.sleep(0)
    assert bus.events == [("memory.updated", {"file": "MEMORY", "op": "add"})]
    rows = _audit_lines(tmp_volume)
    assert len(rows) == 2
    assert rows[0]["file"] == "MEMORY" and rows[0]["op"] == "add" and rows[0]["ok"] is True
    assert rows[0]["before_len"] == 0 and rows[0]["after_len"] == len("- event note\n")
    assert "reason" not in rows[0]
    assert rows[1]["ok"] is True and rows[1]["reason"] == "duplicate" and rows[1]["file"] == "MEMORY"
    for row in rows:
        assert row["ts"].endswith("Z")
        assert set(row) >= {"ts", "file", "op", "ok", "before_len", "after_len"}


# ---------------------------------------------------------------------------
# 8: digest caps + honest budgets


def test_digest_caps_unchanged(tmp_volume: Path) -> None:
    """digest() <=600 each; honest ValueError budgets (memory_chars 2200 etc)."""
    lines = ["- " + "y" * 118 for _ in range(10)]  # 120-char bullets x10
    _seed_memory(tmp_volume, "MEMORY.md", "\n".join(lines) + "\n")
    _seed_memory(tmp_volume, "USER.md", "x" * 2000)
    engine = MemoryEngine(_cfg())
    digest = engine.digest("MEMORY")
    assert len(digest) <= 600
    dlines = digest.split("\n")
    assert len(dlines) == 6
    assert all(len(line) <= 100 for line in dlines)
    # budgets stay honest
    assert engine.fits("MEMORY", "a" * 2200)
    assert not engine.fits("MEMORY", "a" * 2201)
    assert engine.fits("USER", "a" * 1375)
    assert not engine.fits("USER", "a" * 1376)
    assert engine.fits("lessons", "a" * 1500)
    assert not engine.fits("lessons", "a" * 1501)
    with pytest.raises(ValueError, match="unknown file 'NOPE'"):
        engine.digest("NOPE")
