"""Fenced-block parser: one mechanic, three tags (MEMORY_EDIT / SKILL_DRAFT / SUBTASK_DROP).

Implements the shared fenced-block contract of ARCHITECTURE.md sections 2.5,
2.6 and 3.3 (see the approved R3 breakdown): agent replies may embed self-
maintenance directives as fenced code blocks whose info string is one of
:data:`TAGS`. The harness parses these blocks out of a reply's ``final_text``
and strips them from the user-visible reply via :func:`strip_blocks`;
applying them is owned by ``memory.py`` / ``skills.py`` / the subtask inbox,
never by this module — :mod:`harness.blocks` only recognizes and parses, it
never writes.

One mechanic, one parse (P3 work package 3): a single line-scan state machine
over fence lines matched by the module regex ``^```(\\S*)\\s*$`` — three
backticks at column 0, an optional whitespace-free info string, trailing
spaces tolerated, tag match case-sensitive. Every recognized block ends in
exactly one of three states:

- **ok** — recognized tag, fence closed, body parsed: :attr:`Block.fields`
  carries the parsed payload and :attr:`Block.reason` is ``None``.
- **rejected** — recognized tag but unclosed fence or unparseable body: the
  :class:`Block` is still returned (in document order) with ``fields=None``
  and a human-readable ``reason``; nothing is ever raised, a malformed block
  never breaks parsing.
- **ignored** — any other fenced tag (```python, ```FOO): never returned,
  never stripped, passed through byte-identically by design; an unknown-tag
  fence is not recursed into.

Nesting is tracked by line-oriented parity (the R3 breakdown variance): inside
a block (depth 1) any fence line WITH an info string — ```python, even
```MEMORY_EDIT — opens a nested fence (depth 2) whose lines stay part of the
body; a fence WITH info at depth >= 2 is plain body content; a bare ``````
closer at depth >= 2 closes one nested fence; the bare closer at depth 1
closes the block. At EOF with the block still open, the Block is rejected with
reason exactly ``"unclosed fence"``, ``raw`` spans the opener line through EOF
and the remaining text stays inside ``body``.

Per-tag body parsing (rejection keeps the Block with ``reason`` and
``fields=None``):

- MEMORY_EDIT / SUBTASK_DROP — ``yaml.safe_load`` of the body must yield a
  mapping: a YAML error rejects with ``"invalid YAML: <err>"`` and a
  non-mapping body with ``"body is not a YAML mapping"``. ``fields`` is the
  parsed mapping and ``body`` keeps the raw inner text. No semantic
  validation lives here — ``memory.py`` owns the MEMORY_EDIT op/file schema
  and SUBTASK_DROP stays untouched until its consumer.
- SKILL_DRAFT — the body IS a complete SKILL.md document (D5): a ``---``
  line, a YAML frontmatter mapping (requires a non-empty ``str`` ``name``
  plus a ``str`` ``description``; extra frontmatter keys are kept), a second
  ``---`` line, then the markdown body verbatim (embedded code fences
  exercise the nesting rule). ``fields`` is the frontmatter mapping and
  ``body`` keeps the WHOLE document so capture can write it verbatim as a
  valid SKILL.md. Violations reject with ``"missing frontmatter"``,
  ``"invalid frontmatter YAML: <err>"``, ``"frontmatter missing name"`` or
  ``"frontmatter missing description"``.

Byte preservation: ``Block.raw`` is the exact input substring from the first
opener character through the last closer character (the closer line's
terminator excluded). :func:`strip_blocks` removes every recognized block —
accepted AND rejected — by deleting each ``raw`` plus one following ``"\\n"``
when present; every other byte (prose, unknown-tag fences) passes through
untouched, so stripping never reflows the surviving text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import yaml

__all__ = [
    "TAGS",
    "Block",
    "parse_blocks",
    "strip_blocks",
]

_LOG = logging.getLogger("harness.blocks")

# The fence tags this parser knows (case-sensitive); any other fenced tag is
# ignored entirely, by design.
TAGS: frozenset[str] = frozenset({"MEMORY_EDIT", "SKILL_DRAFT", "SUBTASK_DROP"})

# A fence line: three backticks at column 0, optional whitespace-free info
# string, trailing spaces tolerated (R3 breakdown regex).
_FENCE_RE = re.compile(r"```(\S*)\s*")


@dataclass(frozen=True)
class Block:
    """One parsed fenced block (see the module docstring for the contract).

    Attributes:
        tag: the opener's info string, always a member of :data:`TAGS`.
        body: the verbatim inner text between fence lines, individual lines
            joined with ``"\\n"`` (for SKILL_DRAFT the WHOLE document,
            frontmatter and markdown alike).
        raw: the full fenced block including both fence lines, an exact
            substring of the parsed input (closer terminator excluded).
        fields: the parsed structured payload (body YAML mapping for
            MEMORY_EDIT/SUBTASK_DROP, frontmatter mapping for SKILL_DRAFT)
            or ``None`` for a rejected block.
        reason: the rejection reason, or ``None`` iff the block is accepted.
    """

    tag: str
    body: str
    raw: str
    fields: dict | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """True iff the block was accepted (no rejection reason)."""
        return self.reason is None


@dataclass
class _Entry:
    """Internal scan result: one recognized block span inside the text."""

    tag: str
    body: str
    raw: str
    start: int
    end: int
    closed: bool


def _line_content_end(line: str) -> int:
    """Index just past a keepends line's content (its line terminator excluded)."""
    if line.endswith("\r\n"):
        return len(line) - 2
    if line.endswith(("\n", "\r")):
        return len(line) - 1
    return len(line)


def _scan(text: str) -> list[_Entry]:
    """Single line-scan pass over ``text``; returns recognized blocks in
    document order with ``[start, end)`` locating each block in ``text``
    (``end`` excludes a newline following the closer). Unknown-tag fences are
    silently skipped; a block left open at EOF yields ``closed=False`` and
    ``raw`` reaching EOF."""
    entries: list[_Entry] = []
    open_tag: str | None = None
    open_depth = 0
    open_lines: list[str] = []
    open_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        content_end = _line_content_end(line)
        content = line[:content_end]
        match = _FENCE_RE.fullmatch(content)
        if match is None:
            if open_tag is not None:
                open_lines.append(content)
        else:
            info = match.group(1)
            if open_tag is None:
                if info in TAGS:
                    open_tag, open_depth, open_lines, open_start = info, 1, [], offset
            elif info:
                # A fence WITH an info string inside a block never terminates
                # it: at depth 1 it opens a nested fence, deeper it is body.
                if open_depth == 1:
                    open_depth = 2
                open_lines.append(content)
            elif open_depth >= 2:
                open_depth -= 1
                open_lines.append(content)
            else:
                end = offset + content_end
                entries.append(
                    _Entry(
                        open_tag,
                        "\n".join(open_lines),
                        text[open_start:end],
                        open_start,
                        end,
                        True,
                    )
                )
                open_tag = None
        offset += len(line)
    if open_tag is not None:
        entries.append(
            _Entry(open_tag, "\n".join(open_lines), text[open_start:], open_start, len(text), False)
        )
    return entries


def _parse_mapping_body(body: str) -> tuple[str | None, dict | None]:
    """Parse a MEMORY_EDIT/SUBTASK_DROP body: ``yaml.safe_load`` must return
    a mapping (per-tag schema validation belongs to the tag consumers)."""
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return f"invalid YAML: {exc}", None
    if not isinstance(parsed, dict):
        return "body is not a YAML mapping", None
    return None, parsed


def _parse_skill_draft(body: str) -> tuple[str | None, dict | None]:
    """Parse a SKILL_DRAFT body: ``---``-delimited YAML frontmatter with a
    non-empty str ``name`` and a str ``description`` (extra keys kept); the
    fields are exactly the frontmatter mapping, the body stays verbatim."""
    lines = body.split("\n")
    if lines[0] != "---":
        return "missing frontmatter", None
    try:
        close = lines.index("---", 1)
    except ValueError:
        return "missing frontmatter", None
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:close]))
    except yaml.YAMLError as exc:
        return f"invalid frontmatter YAML: {exc}", None
    name = frontmatter.get("name") if isinstance(frontmatter, dict) else None
    if not isinstance(name, str) or not name:
        return "frontmatter missing name", None
    if not isinstance(frontmatter.get("description"), str):
        return "frontmatter missing description", None
    return None, frontmatter


def _parse_body(tag: str, body: str) -> tuple[str | None, dict | None]:
    """Tag dispatch for body parsing; returns ``(reason, fields)`` with
    exactly one side set (accepted: ``(None, mapping)``, rejected: the other)."""
    if tag == "SKILL_DRAFT":
        return _parse_skill_draft(body)
    return _parse_mapping_body(body)


def parse_blocks(text: str) -> list[Block]:
    """Scan ``text`` and return every recognized block in document order —
    accepted AND rejected ones (unknown-tag fences never appear). Rejection
    is a :class:`Block` with ``fields=None`` and a ``reason`` string; parsing
    never raises on malformed input."""
    blocks: list[Block] = []
    for entry in _scan(text):
        if entry.closed:
            reason, fields = _parse_body(entry.tag, entry.body)
        else:
            reason, fields = "unclosed fence", None
        blocks.append(
            Block(tag=entry.tag, body=entry.body, raw=entry.raw, fields=fields, reason=reason)
        )
    return blocks


def strip_blocks(text: str) -> str:
    """Remove every recognized block (``parse_blocks`` results, accepted or
    rejected) from ``text``: each Block's ``raw`` plus one following ``"\\n"``
    when present is deleted; all other bytes — prose and unknown-tag fences —
    pass through unchanged."""
    pieces: list[str] = []
    position = 0
    for entry in _scan(text):
        pieces.append(text[position:entry.start])
        position = entry.end
        if text[position:position + 2] == "\r\n":
            position += 2
        elif position < len(text) and text[position] == "\n":
            position += 1
    pieces.append(text[position:])
    return "".join(pieces)
