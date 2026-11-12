"""Tests for harness.persist (JSONL appends, torn-line reads, atomic counters)."""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterator

import pytest

import harness.persist
from harness.persist import AtomicCounter, JsonlWriter, MarkdownAppender, append_text, read_jsonl


@pytest.fixture
def fsync_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    calls: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(harness.persist.os, "fsync", counting_fsync)
    yield calls


def test_appender_and_writer_do_not_create_file_at_construction(tmp_path: pathlib.Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    markdown_path = tmp_path / "notes.md"
    counter_path = tmp_path / "budgets.json"
    JsonlWriter(jsonl_path)
    MarkdownAppender(markdown_path)
    AtomicCounter(counter_path, initial={"n": 1})
    assert not jsonl_path.exists()
    assert not markdown_path.exists()
    assert not counter_path.exists()


def test_writer_creates_parent_dirs_lazily_on_first_append(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "deep" / "nested" / "events.jsonl"
    writer = JsonlWriter(path)
    assert not path.parent.exists()
    assert writer.append({"n": 1}) == len(b'{"n":1}\n')
    assert path.parent.is_dir()
    assert path.read_bytes() == b'{"n":1}\n'


def test_append_writes_one_compact_newline_terminated_line_per_record(
    tmp_path: pathlib.Path,
    fsync_calls: list[int],
) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlWriter(path)
    assert not path.exists()
    first = {"a": 1, "b": "café→🚀", "nested": {"list": [1, 2, 3]}}
    expected_first = (
        json.dumps(first, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    assert writer.append(first) == len(expected_first)
    assert writer.append({"c": 2}) == len(b'{"c":2}\n')
    content = path.read_bytes()
    assert content == expected_first + b'{"c":2}\n'
    assert content.count(b"\n") == 2
    assert content.endswith(b"\n")
    assert len(fsync_calls) >= 2


def test_read_jsonl_roundtrip_preserves_unicode(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlWriter(path)
    records = [
        {"text": "héllo → 🚀", "nested": {"key_日本": "値"}},
        {"n": 1, "list": [True, None, 3.5], "empty": "", "raw": '"quoted\\backslash"'},
    ]
    for record in records:
        writer.append(record)
    assert read_jsonl(path) == records


def test_read_jsonl_ignores_torn_final_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlWriter(path)
    writer.append({"n": 1})
    writer.append({"n": 2})
    with open(path, "ab") as handle:
        handle.write(b'{"n": 3, "torn')
    assert read_jsonl(path) == [{"n": 1}, {"n": 2}]


def test_read_jsonl_raises_on_corrupt_non_last_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlWriter(path)
    writer.append({"n": 1})
    with open(path, "ab") as handle:
        handle.write(b"{corrupt\n")
    writer.append({"n": 3})
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)


def test_read_jsonl_raises_value_error_on_non_dict_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "numbers.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON object") as excinfo:
        read_jsonl(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "line 1" in message


def test_read_jsonl_skips_blank_lines(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "sparse.jsonl"
    path.write_text('{"a": 1}\n\n   \n{"b": 2}\n\n', encoding="utf-8")
    assert read_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_read_jsonl_missing_file_returns_empty_list(tmp_path: pathlib.Path) -> None:
    assert read_jsonl(tmp_path / "absent.jsonl") == []


def test_markdown_appender_byte_exact(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "notes.md"
    appender = MarkdownAppender(path)
    assert appender.append("héllo") == len("héllo".encode("utf-8"))
    assert appender.append(" ") == 1
    assert appender.append_line("line1") == len(b"line1\n")
    assert appender.append_line("line2\n") == len(b"line2\n")
    assert path.read_text(encoding="utf-8") == "héllo line1\nline2\n"


def test_append_text_function_form(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "a.md"
    assert append_text(path, "exactly\n") == len(b"exactly\n")
    assert path.read_bytes() == b"exactly\n"


def test_counter_bump_rewrites_valid_single_line_without_tmp_leftovers(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "budgets.json"
    counter = AtomicCounter(path)
    counter.save()
    counter.bump("requests")
    counter.bump("requests", 4)
    content = path.read_text(encoding="utf-8")
    assert json.loads(content) == {"requests": 5}
    assert content == '{"requests":5}\n'
    assert [leftover.name for leftover in path.parent.glob("*.tmp")] == []


def test_counter_set_merges_and_save_rewrites(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "budgets.json"
    counter = AtomicCounter(path, initial={"a": 1})
    assert counter.set({"b": 2}) == {"a": 1, "b": 2}
    assert counter.save() == {"a": 1, "b": 2}
    assert path.read_text(encoding="utf-8") == '{"a":1,"b":2}\n'
    assert [leftover.name for leftover in path.parent.glob("*.tmp")] == []


def test_counter_torn_file_at_construction_falls_back_to_initial(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "budgets.json"
    path.write_text('{"a": 1, "torn', encoding="utf-8")
    counter = AtomicCounter(path, initial={"n": 7})
    assert counter.load() == {"n": 7}


def test_counter_load_falls_back_to_last_known_good_on_torn_read(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "budgets.json"
    counter = AtomicCounter(path, initial={"a": 1})
    counter.set({"a": 3})
    path.write_text('{"a": 4, "torn', encoding="utf-8")
    assert counter.load() == {"a": 3}


def test_counter_get_and_bump_semantics(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "budgets.json"
    counter = AtomicCounter(path, initial={"n": 2})
    assert counter.get("n") == 2
    assert counter.get("missing", 9) == 9
    assert counter.bump("n") == {"n": 3}
    assert counter.get("n") == 3
    assert counter.bump("missing", 5)["missing"] == 5
    assert json.loads(path.read_text(encoding="utf-8"))["n"] == 3
