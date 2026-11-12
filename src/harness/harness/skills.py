"""Skills engine: deterministic INDEX.md builder, body loader, SKILL_DRAFT capture.

Implements ARCHITECTURE.md sections 2.6 and 3.3 plus DECISIONS.md D5
(Anthropic Agent Skills shape): a skill is a folder under
``<storage.data_dir>/skills/`` containing a ``SKILL.md`` whose body is a
``---``-YAML-frontmatter + markdown document (both agent CLIs read this
format natively). Progressive disclosure comes in two levels:

- **Level 1 (always loaded):** a compact generated ``INDEX.md`` — one
  ``"- <name>: <description[:57]>"`` entry per skill in folder-name order,
  byte-deterministic, capped by ``cfg.memory.budgets.index_chars`` with an
  HONEST overflow error (never a silently truncated list), rewritten
  atomically only when the generated content differs from the on-disk bytes.
- **Level 2 (on demand):** :meth:`SkillsEngine.get_skill_body` returns the
  SKILL.md body after the frontmatter, hard-truncated to
  :data:`SKILL_BODY_MAX_CHARS` with :data:`TRUNCATION_MARKER` appended.

:meth:`SkillsEngine.capture` applies a parsed ``SKILL_DRAFT`` block (the
body IS a complete SKILL.md document, written VERBATIM): the folder name is
the slug of the frontmatter ``name``, collisions bump ``-2``/``-3`` suffixes
(never overwriting), the index is rebuilt afterwards, and a ``skill.captured``
bus event fires through the private :func:`_emit` helper (same shape as in
:mod:`harness.memory`: fire-and-forget into a running loop only, silent
no-op without one, bus failures never propagate).

``CAPTURE_PROMPT_TEMPLATE`` is the exact distillation prompt consumed by the
R9/R12 wiring (not by this module): ``<name>`` is filled via
``.replace("<name>", name)`` — never ``str.format``, bodies may contain
braces.

The skills directory is derived from ``cfg.storage.data_dir`` — there is NO
``storage.skills_dir`` config key (``extra="forbid"``); the R1 supervisor
seeds ``INDEX.md`` at exactly this path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from harness.config import HarnessConfig

__all__ = [
    "CAPTURE_PROMPT_TEMPLATE",
    "SKILL_BODY_MAX_CHARS",
    "TRUNCATION_MARKER",
    "SkillsEngine",
]

# Module logger (pattern: harness.memory); deterministic skips warn here.
_LOG = logging.getLogger("harness.skills")

# Level-2 body cap: SKILL.md bodies are hard-truncated to this many chars
# before being appended to agent prompts (ARCHITECTURE.md section 2.6).
SKILL_BODY_MAX_CHARS: int = 4000

# Appended after the truncation cut so consumers can see the clip happened.
TRUNCATION_MARKER: str = "\n…[truncated]"

# Index header (fixed, no timestamps — byte determinism across rebuilds).
_INDEX_HEADER = "# Skill index (generated)"

# Longest skill description excerpt kept in an index entry.
_INDEX_DESCRIPTION_CAP = 57

# Slug rule (breakdown section 5): lowercase, [^a-z0-9]+ -> "-", strip dashes.
_SLUG_RE = re.compile(r"[^a-z0-9-]+")

CAPTURE_PROMPT_TEMPLATE = """\
Distill this session into SKILL.md for skill '<name>'.

From the session transcript, extract the durable, reusable procedure: what was done,
in which order, with which exact commands, paths and tools, and which pitfalls to
avoid. Emit ONE fenced block tagged SKILL_DRAFT and nothing else:

```SKILL_DRAFT
---
name: <name>
description: <one-line summary>
---
<markdown body: numbered steps, commands in code fences, pitfalls>
```
"""


def _emit(bus: Any | None, topic: str, payload: dict) -> None:
    """Best-effort bus emission, same shape as in :mod:`harness.memory`.

    Fire-and-forget into the RUNNING event loop only; with no bus and/or no
    running loop (``RuntimeError``) this is a silent no-op — capture never
    blocks on (or creates) a loop. Bus failures inside the fired task are
    swallowed and logged so they never propagate into the caller.
    """
    if bus is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _publish() -> None:
        try:
            await bus.publish(topic, dict(payload))
        except Exception as exc:  # noqa: BLE001 — bus failures must not escape
            _LOG.warning("bus publish failed for %s: %s", topic, exc)

    loop.create_task(_publish())


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically rewrite ``path`` with ``text`` (documented AtomicCounter
    pattern: write ``<path>.tmp`` -> fsync -> ``os.replace`` -> fsync the
    parent directory fd, tolerating directory-fsync-less filesystems)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    data = text.encode("utf-8")
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


def _parse_frontmatter(text: str) -> tuple[str | None, dict | None]:
    """Read a SKILL.md frontmatter: the first ``---`` line through the next
    ``---`` line parsed with ``yaml.safe_load`` (a mapping). Returns
    ``(reason, fields)`` — exactly one side set. Frontmatter shape checks
    (name/description presence) are the caller's concern."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return "missing frontmatter", None
    try:
        close = lines.index("---", 1)
    except ValueError:
        return "missing frontmatter", None
    try:
        parsed = yaml.safe_load("\n".join(lines[1:close]))
    except yaml.YAMLError as exc:
        return f"invalid frontmatter YAML: {exc}", None
    if not isinstance(parsed, dict):
        return "frontmatter is not a YAML mapping", None
    return None, parsed


class SkillsEngine:
    """Builds the level-1 skill index and applies ``SKILL_DRAFT`` captures.

    One engine per process, constructed from the harness configuration (the
    index budget via ``cfg.memory.budgets.index_chars``) and an optional bus
    for ``skill.captured`` fan-out. All operations are synchronous and the
    index build is fully deterministic: parse -> format -> compare -> atomic
    rewrite only when the bytes differ.
    """

    def __init__(self, cfg: HarnessConfig, bus: Any | None = None) -> None:
        self._cfg = cfg
        self._bus = bus
        # Derived from data_dir on purpose: NO storage.skills_dir config key.
        self._root = Path(cfg.storage.data_dir) / "skills"

    # ------------------------------------------------------------------
    # Level 1: the generated INDEX.md
    # ------------------------------------------------------------------
    def build_index(self) -> str:
        """Scan ``<skills_dir>/*/SKILL.md`` (one level, folder-name order)
        and regenerate ``INDEX.md``; return the generated index text.

        Each kept skill contributes one ``"- <slug>: <description[:57]>"``
        entry (the slug is the folder name, i.e. exactly what
        :meth:`get_skill_body` resolves) under the fixed header. A folder
        without SKILL.md, with unparseable frontmatter, or missing
        ``name``/``description`` is skipped with a logged warning (deterministic; never a crash). A
        generated index longer than ``index_chars`` raises an HONEST
        :class:`ValueError` naming the skill count and size — the list is
        never truncated. The file is rewritten (atomic tmp + fsync +
        ``os.replace`` + dir-fd fsync) ONLY when the on-disk bytes differ,
        so repeated rebuilds are no-ops.
        """
        folders: list[Path] = []
        if self._root.is_dir():
            folders = sorted(
                (entry for entry in self._root.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name,
            )
        entries: list[str] = []
        for folder in folders:
            skill_md = folder / "SKILL.md"
            try:
                text = skill_md.read_text(encoding="utf-8")
                reason, fields = _parse_frontmatter(text)
            except (OSError, UnicodeDecodeError) as exc:
                reason, fields = str(exc), None
            if reason is not None or fields is None:
                _LOG.warning("skill '%s' skipped: %s", folder.name, reason)
                continue
            name = fields.get("name")
            if not isinstance(name, str) or not name.strip():
                _LOG.warning("skill '%s' skipped: frontmatter missing name", folder.name)
                continue
            description = fields.get("description")
            if not isinstance(description, str):
                _LOG.warning("skill '%s' skipped: frontmatter missing description", folder.name)
                continue
            # Entries carry the folder slug (what get_skill_body resolves), so a
            # draft named "ship containers" lands as "- ship-containers: ..." —
            # level-1 lines always map 1:1 onto a body lookup.
            entries.append(f"- {folder.name}: {description[:_INDEX_DESCRIPTION_CAP]}")
        content = "\n".join([_INDEX_HEADER, *entries]) + "\n"
        budget = int(self._cfg.memory.budgets.index_chars)
        if len(content) > budget:
            raise ValueError(
                f"skills index budget {budget} exceeded: {len(entries)} skills, "
                f"{len(content)} chars (prune skill descriptions)"
            )
        index_path = self._root / "INDEX.md"
        written = False
        try:
            existing = index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        if existing != content:
            _atomic_write_text(index_path, content)
            written = True
        _LOG.debug("build_index: skills=%d chars=%d rewritten=%s", len(entries), len(content),
                   written)
        return content

    # ------------------------------------------------------------------
    # Level 2: full body on demand
    # ------------------------------------------------------------------
    def get_skill_body(self, name: str) -> str:
        """Return the SKILL.md body of skill ``name`` (the folder-slug).

        The body is all text AFTER the frontmatter closing ``---`` line (the
        whole file when there is none), hard-truncated to
        :data:`SKILL_BODY_MAX_CHARS` with :data:`TRUNCATION_MARKER` appended
        when clipping happens. Returns ``""`` for an unknown skill.
        """
        skill_md = self._root / name / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        lines = text.split("\n")
        if lines and lines[0] == "---":
            try:
                close = lines.index("---", 1)
            except ValueError:
                body = text  # unclosed frontmatter degenerates to the whole text
            else:
                body = "\n".join(lines[close + 1:])
        else:
            body = text
        if len(body) > SKILL_BODY_MAX_CHARS:
            body = body[:SKILL_BODY_MAX_CHARS] + TRUNCATION_MARKER
        return body

    # ------------------------------------------------------------------
    # Capture loop: apply a SKILL_DRAFT block (ARCHITECTURE.md section 3.3)
    # ------------------------------------------------------------------
    def capture(self, session_id: str, draft: Any,
                distill: Callable[[str, str], str] | None = None) -> dict:
        """Apply one ``SKILL_DRAFT`` block; return ``{slug, path, created_new}``.

        The draft must be an ok SKILL_DRAFT :class:`~harness.blocks.Block`
        (its body is a complete SKILL.md document, written verbatim) —
        anything else raises :class:`ValueError` and writes NOTHING. The
        folder name is the slug of the ORIGINAL frontmatter ``name`` even
        when ``distill`` rewrites the document (``body =
        distill(session_id, draft.body)``). Collisions bump ``-2``/``-3``...
        suffixes to the first free folder. The index is rebuilt AFTER the
        write and ``skill.captured`` {skill_name, path} is then emitted
        best-effort on the bus.
        """
        if draft.tag != "SKILL_DRAFT" or not draft.ok or not isinstance(draft.fields, dict):
            raise ValueError("capture expects an ok SKILL_DRAFT block")
        fields = draft.fields
        name = fields.get("name")
        if not isinstance(name, str):
            # An ill-typed draft is a rejection, not a program error.
            raise ValueError(  # noqa: TRY004 — breakdown: ValueError for every bad draft
                "capture expects an ok SKILL_DRAFT block"
            )
        slug = _SLUG_RE.sub("-", name.lower()).strip("-")
        if not slug:
            raise ValueError(f"cannot slugify skill name '{name}'")
        body = draft.body if distill is None else distill(session_id, draft.body)
        created_new = False
        target = self._root / slug
        if (target / "SKILL.md").exists():
            bump = 2
            while (self._root / f"{slug}-{bump}" / "SKILL.md").exists():
                bump += 1
            target = self._root / f"{slug}-{bump}"
        else:
            created_new = True
        skill_path = target / "SKILL.md"
        _atomic_write_text(skill_path, body)
        self.build_index()
        _emit(self._bus, "skill.captured", {"skill_name": slug, "path": str(skill_path)})
        return {"slug": target.name, "path": str(skill_path), "created_new": created_new}
