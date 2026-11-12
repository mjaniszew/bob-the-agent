"""Tests for harness.blocks (fence state machine, per-tag body parsing, strip_blocks)."""

from __future__ import annotations

from harness.blocks import TAGS, Block, parse_blocks, strip_blocks

_MEM_BODY = "op: add\nfile: MEMORY\ncontent: prefers dark mode"
_MEM_ADD = "```MEMORY_EDIT\n" + _MEM_BODY + "\n```"

_SKILL_DOC = (
    "---\n"
    "name: deploy-flask\n"
    "description: Deploy the Flask service to production\n"
    "---\n"
    "## Steps\n"
    "Run the deploy script."
)
_SKILL_OK = "```SKILL_DRAFT\n" + _SKILL_DOC + "\n```"

_SUBTASK_DOC = "description: deploy the stack\nparent: T1"
_SUBTASK = "```SUBTASK_DROP\n" + _SUBTASK_DOC + "\n```"

_FOO_FENCE = "```FOO\nsecret unknown-tag content\n```"


def test_memory_edit_happy() -> None:
    assert TAGS == frozenset({"MEMORY_EDIT", "SKILL_DRAFT", "SUBTASK_DROP"})
    text = "Before.\n" + _MEM_ADD + "\nAfter.\n"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, Block)
    assert block.tag == "MEMORY_EDIT"
    assert block.ok
    assert block.reason is None
    assert block.fields == {"op": "add", "file": "MEMORY", "content": "prefers dark mode"}
    assert block.body == _MEM_BODY
    assert block.raw == _MEM_ADD
    assert _MEM_ADD in text  # raw is the byte-exact substring of the input


def test_skill_draft_happy() -> None:
    blocks = parse_blocks("Prose.\n" + _SKILL_OK + "\nTail.")
    assert len(blocks) == 1
    block = blocks[0]
    assert block.tag == "SKILL_DRAFT"
    assert block.ok
    assert block.fields == {"name": "deploy-flask", "description": "Deploy the Flask service to production"}
    assert block.body == _SKILL_DOC  # frontmatter + markdown kept verbatim
    assert block.raw == _SKILL_OK


def test_subtask_drop_happy() -> None:
    blocks = parse_blocks("Intro.\n" + _SUBTASK + "\nOutro.")
    assert len(blocks) == 1
    block = blocks[0]
    assert block.tag == "SUBTASK_DROP"
    assert block.ok
    assert block.fields == {"description": "deploy the stack", "parent": "T1"}
    assert block.body == _SUBTASK_DOC  # body kept raw, no other semantics


def test_multiple_blocks_in_order() -> None:
    text = "Intro prose.\n" + _MEM_ADD + "\nMiddle prose.\n" + _SKILL_OK + "\n" + _SUBTASK + "\nOutro prose.\n"
    blocks = parse_blocks(text)
    assert [block.tag for block in blocks] == ["MEMORY_EDIT", "SKILL_DRAFT", "SUBTASK_DROP"]
    assert all(block.ok for block in blocks)
    assert all("prose" not in block.body for block in blocks)
    assert [block.raw for block in blocks] == [_MEM_ADD, _SKILL_OK, _SUBTASK]


def test_nested_info_fence_stays_in_body() -> None:
    text = (
        "Before.\n"
        "```SKILL_DRAFT\n"
        "---\n"
        "name: nested\n"
        "description: skill with a code fence inside\n"
        "---\n"
        "Intro line.\n"
        "```python\n"
        "print('hi')\n"
        "```\n"
        "Closing line.\n"
        "```\n"
        "After.\n"
    )
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.tag == "SKILL_DRAFT"
    assert block.ok
    assert block.fields == {"name": "nested", "description": "skill with a code fence inside"}
    assert block.body == (
        "---\n"
        "name: nested\n"
        "description: skill with a code fence inside\n"
        "---\n"
        "Intro line.\n"
        "```python\n"
        "print('hi')\n"
        "```\n"
        "Closing line."
    )
    assert block.raw.startswith("```SKILL_DRAFT\n")
    assert block.raw.endswith("Closing line.\n```")


def test_two_nested_pairs_still_one_block() -> None:
    text = (
        "```SKILL_DRAFT\n"
        "---\n"
        "name: pairs\n"
        "description: two nested fence pairs inside\n"
        "---\n"
        "```python\n"
        "first\n"
        "```\n"
        "```python\n"
        "second\n"
        "```\n"
        "between\n"
        "```\n"
    )
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.tag == "SKILL_DRAFT"
    assert block.ok
    assert block.fields == {"name": "pairs", "description": "two nested fence pairs inside"}
    assert block.body == (
        "---\n"
        "name: pairs\n"
        "description: two nested fence pairs inside\n"
        "---\n"
        "```python\n"
        "first\n"
        "```\n"
        "```python\n"
        "second\n"
        "```\n"
        "between"
    )


def test_unclosed_fence_rejected() -> None:
    text = "```MEMORY_EDIT\nop: add\nfile: MEMORY\ncontent: half\nthis line after opener is still body-so-far"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert not block.ok
    assert block.reason == "unclosed fence"
    assert block.fields is None
    assert block.raw == text  # raw spans the opener line through EOF
    assert block.body.startswith("op: add")
    assert "this line after opener is still body-so-far" in block.body


def test_bad_yaml_rejected() -> None:
    text = "```MEMORY_EDIT\nop: [unclosed\n```"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert not block.ok
    assert "invalid YAML" in (block.reason or "")
    assert block.fields is None
    assert block.body == "op: [unclosed"
    assert block.raw == text


def test_non_mapping_body_rejected() -> None:
    blocks = parse_blocks("```MEMORY_EDIT\n- just\n- a list\n```")
    assert len(blocks) == 1
    assert not blocks[0].ok
    assert blocks[0].reason == "body is not a YAML mapping"
    assert blocks[0].fields is None
    scalar = parse_blocks("```SUBTASK_DROP\njust a plain string\n```")
    assert len(scalar) == 1
    assert scalar[0].reason == "body is not a YAML mapping"


def test_skill_draft_sad_shapes() -> None:
    cases = [
        ("```SKILL_DRAFT\nname: x\ndescription: y\n```", "missing frontmatter"),
        ("```SKILL_DRAFT\n---\ndescription: y\n---\ndoc body\n```", "frontmatter missing name"),
        ("```SKILL_DRAFT\n---\nname: x\n---\ndoc body\n```", "frontmatter missing description"),
        ("```SKILL_DRAFT\n---\nname: 7\ndescription: y\n---\ndoc body\n```", "frontmatter missing name"),
        ("```SKILL_DRAFT\n---\nname: x\ndescription: y\nno closing fence", "unclosed fence"),
    ]
    for text, expected_reason in cases:
        blocks = parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].reason == expected_reason
        assert blocks[0].fields is None
        assert not blocks[0].ok


def test_unknown_tags_ignored() -> None:
    text = "Intro.\n```python\nprint('x')\n```\n\n```FOO\nsecret unknown-tag content\n```\nOutro."
    assert parse_blocks(text) == []
    assert parse_blocks("") == []


def test_strip_blocks_removes_and_preserves() -> None:
    text = (
        "Start\n"
        + _FOO_FENCE + "\n"
        + _MEM_ADD + "\n"
        + "```SUBTASK_DROP\nop: [broken\n```\n"
        + "```SKILL_DRAFT\n---\nname: x\ndescription: y\n---\nnever closed"
    )
    parsed = parse_blocks(text)
    assert [block.tag for block in parsed] == ["MEMORY_EDIT", "SUBTASK_DROP", "SKILL_DRAFT"]
    assert [block.ok for block in parsed] == [True, False, False]
    assert "invalid YAML" in (parsed[1].reason or "")
    assert parsed[2].reason == "unclosed fence"
    stripped = strip_blocks(text)
    assert stripped == "Start\n```FOO\nsecret unknown-tag content\n```\n"
    for tag in ("MEMORY_EDIT", "SKILL_DRAFT", "SUBTASK_DROP"):
        assert tag not in stripped


def test_strip_preserves_surroundings() -> None:
    block = "```MEMORY_EDIT\nop: remove\nfile: USER\npattern: stale note\n```"
    assert strip_blocks("A\n" + block + "\nB") == "A\nB"
    blocks = parse_blocks(block + "\n")
    assert len(blocks) == 1
    assert blocks[0].ok
    assert blocks[0].fields == {"op": "remove", "file": "USER", "pattern": "stale note"}
    assert strip_blocks(block + "\n") == ""


def test_crlf_input_robustness() -> None:
    crlf_block = _MEM_ADD.replace("\n", "\r\n")
    text = "Before.\r\n" + crlf_block + "\r\nAfter.\r\n"
    blocks = parse_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.ok
    assert block.fields == {"op": "add", "file": "MEMORY", "content": "prefers dark mode"}
    assert block.body == _MEM_BODY
    assert block.raw == crlf_block
    assert strip_blocks(text) == "Before.\r\nAfter.\r\n"
