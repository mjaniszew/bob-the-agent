"""Fake (scripted) agent adapter for tests and dry runs (ARCHITECTURE.md section 2.2).

:class:`FakeAdapter` implements :class:`~harness.adapters.base.AgentRun` without
ever spawning a process: it replays a scripted event stream so downstream
consumers (and the R6 orchestrator) can be exercised deterministically. The
scenario is loaded from a YAML or JSON file (``yaml.safe_load`` accepts both)
or passed inline as a mapping:

.. code-block:: yaml

    events:                    # yielded in order, each after ``delay_s``
      - kind: chunk            # chunk / tool / final / failed / system / started
        text: "hello"
      - kind: tool
        tool_name: bash
        tool_brief: "echo hi"
      - kind: final
        final_text: "hello world"
    delay_s: 0.0               # tiny inter-event delay (default 0)
    failed_after: null         # or seconds: sleep, then failed('timeout simulation')
    exit_code: 0               # if != 0 the terminal event is failed(exit_code)
    argv_when_run:             # assertions mode: streamed as the FIRST system
      - "hermes"               # event, json.dumps-ed, so argv-snapshot tests can
                               # inspect what the REAL adapter would assemble

Stream shape: ``argv_when_run`` system event (optional) first, then the
scripted events in order, then a terminal event — one of
``failed('timeout simulation')`` when ``failed_after`` is set, ``failed`` when
``exit_code != 0``, the scripted ``final`` event (if the script has one), or a
synthesized ``final`` whose ``final_text`` joins the scripted chunk texts.
``argv_when_run`` is NOT redaction-checked: it contains no secrets (the real
adapters' argv carries none either — query-file/``--dir`` paths are fine).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from harness.adapters.base import RunSpec, StreamEvent

__all__ = ["FakeAdapter"]

# StreamEvent fields a scripted event dict may carry (besides ``kind``).
_EVENT_FIELDS: tuple[str, ...] = (
    "text",
    "final_text",
    "tool_name",
    "tool_brief",
    "error",
    "exit_code",
    "session_ref",
    "raw",
)


class FakeAdapter:
    """Scripted-stream adapter: zero processes, deterministic replay."""

    def __init__(self, scenario: str | Path | Mapping[str, Any]) -> None:
        """Load the scenario mapping from a YAML/JSON file path or inline.

        Raises ``ValueError`` on a non-mapping scenario, a scripted event
        without a ``kind`` (or with a bogus kind — the closed set check in
        :class:`StreamEvent` fires at load time), or non-integer
        ``exit_code``/non-numeric ``delay_s``/``failed_after`` values.
        """
        if isinstance(scenario, (str, Path)):
            with open(scenario, "r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
        else:
            loaded = scenario
        if not isinstance(loaded, Mapping):
            raise TypeError(
                "FakeAdapter scenario must be a mapping (events/failed_after/"
                f"exit_code/argv_when_run), got {type(loaded).__name__}"
            )
        raw_events = loaded.get("events", [])
        if raw_events is None:
            raw_events = []
        self.events: list[StreamEvent] = []
        for index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, Mapping) or "kind" not in raw_event:
                raise ValueError(
                    f"FakeAdapter event #{index} must be a mapping with a 'kind' key"
                )
            payload: dict[str, Any] = {"kind": str(raw_event["kind"])}
            for field_name in _EVENT_FIELDS:
                if raw_event.get(field_name) is not None:
                    payload[field_name] = raw_event[field_name]
            self.events.append(StreamEvent(**payload))
        self.delay_s = float(loaded.get("delay_s", 0) or 0)
        self.failed_after = loaded.get("failed_after")
        if self.failed_after is not None:
            self.failed_after = float(self.failed_after)
        exit_code = loaded.get("exit_code", 0)
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise TypeError(f"FakeAdapter exit_code must be a int, got {exit_code!r}")
        self.exit_code = exit_code
        self.argv_when_run = list(loaded["argv_when_run"]) if loaded.get("argv_when_run") else None

    async def run(self, spec: RunSpec) -> AsyncIterator[StreamEvent]:
        """Yield the scripted stream (assert-able, no process spawn).

        ``spec`` is accepted for the agent protocol and is intentionally
        ignored (the scenario fully describes the stream). The terminal-event
        rules are documented in the module docstring.
        """
        if self.argv_when_run:
            yield StreamEvent(kind="system", raw=json.dumps(self.argv_when_run))
        for event in self.events:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield event
        if self.failed_after is not None:
            await asyncio.sleep(self.failed_after)
            yield StreamEvent(kind="failed", error="timeout simulation")
        elif self.exit_code != 0:
            yield StreamEvent(
                kind="failed",
                error=f"exit code {self.exit_code}",
                exit_code=self.exit_code,
            )
        elif any(event.kind == "final" for event in self.events):
            pass  # the scripted final is the terminal event
        else:
            chunks = "".join(
                event.text or "" for event in self.events if event.kind == "chunk"
            )
            yield StreamEvent(kind="final", final_text=chunks)
