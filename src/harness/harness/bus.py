"""In-process asyncio pub/sub event bus (ARCHITECTURE.md section 2.3).

The bus is how harness components talk: any component publishes a topic/event
and every component that needs a copy of the traffic subscribes a *sink* (one
web SSE queue per open client, the Discord bot, the transcript writer, the
session manager, ...). Design point D3 verdict: a plain in-process
:mod:`asyncio` pub/sub — no NATS/Redis/MQTT — because the whole harness
lives in one container. Everything here is async-only: no threads, no locks
(the single event loop owns all state; the fan-out contains zero awaits, so
publishers are never backpressured by slow consumers).

Contract and design choices:

- **Topics are a closed registry** (``TOPICS``): the 17 rev2 topics of
  section 2.3 (``user.msg`` ... ``skill.captured``), each mapping to a
  documented tuple of payload keys. Publishing an unknown topic raises
  :class:`ValueError` naming the topic before any fan-out; a payload that is
  not a ``dict`` raises :class:`TypeError` (payload keys themselves are
  documentation, not runtime-checked, in the PoC). Two value restrictions
  live in the registry: ``gate.notify``'s ``kind`` key carries
  ``"open" | "re-open" | "expired"`` and ``reply.chunk``'s ``source`` key
  carries ``"agent" | "router" | "system"``.
- **Sinks are named queues, not topics**: :meth:`Bus.subscribe` registers one
  bounded :class:`asyncio.Queue` under a unique sink name and returns it. A
  sink receives ``tuple[topic, payload]`` events for **every** published
  topic — exactly once per event per sink, in publish order. Subscribing a
  name twice raises :class:`ValueError`.
- **Lossy-safe fan-out**: each publish copies the payload per sink
  (:func:`dict` shallow copy), so neither the publisher mutating the dict it
  handed over nor a consumer mutating the copy it received can leak into
  other sinks. Fan-out uses ``put_nowait`` only: a slow or stalled consumer
  gets its events **dropped** when its queue is full instead of ever
  blocking a publisher. The first overflow of a sink warns once
  (``overflow: sink=... topic=... dropped=...`` on the ``harness.bus``
  logger) and emits one best-effort ``system.log`` warning event through an
  internal silent path (drops in that path are neither counted nor warned,
  so no recursion is possible); later overflows for the same sink only bump
  the cumulative counter reported by the ``overflow_counts`` property.
- **Shutdown helpers**: :meth:`Bus.drain` and :meth:`Bus.aclose` are
  idempotent. ``drain`` polls sink queues until they are empty (up to
  ``timeout_s``) and then unsubscribes every sink; both leave the consumer
  queues themselves intact — they stay with their consumers for the garbage
  collector. Overflow counters survive a close for observability.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType

__all__ = ["DEFAULT_QUEUE_SIZE", "TOPICS", "Bus", "Subscriber"]

_LOG = logging.getLogger("harness.bus")

DEFAULT_QUEUE_SIZE: int = 512

TOPICS: dict[str, tuple[str, ...]] = {
    "user.msg": ("channel", "channel_handle", "text", "files", "session_id?"),
    "route.decided": ("session_id", "route"),
    "task.appeared": ("task_id", "path"),
    "task.state": ("task_id", "old", "new", "reason?"),
    "gate.opened": ("task_id", "question"),
    "gate.answered": ("task_id", "answer", "channel"),
    "gate.notify": ("task_id", "question", "kind"),
    "schedule.fired": ("job_id", "job_kind", "outcome"),
    "agent.event": ("session_id", "event"),
    "agent.done": ("session_id", "final_text"),
    "session.status": ("session_id", "old", "new"),
    "reply.chunk": ("session_id", "seq", "text", "source"),
    "reply.final": ("session_id", "text", "meta"),
    "system.log": ("level", "msg", "data?"),
    "handoff.request": ("from", "to", "prompt"),
    "memory.updated": ("file", "op"),
    "skill.captured": ("skill_name", "path"),
}


class Bus:
    """Fan-out hub delivering every published topic to every sink queue.

    Sinks are registered under unique names via :meth:`subscribe` (or the
    :meth:`subscriber` context manager) and receive
    ``tuple[topic, payload]`` events on a bounded :class:`asyncio.Queue`
    sized ``queue_size`` (default :data:`DEFAULT_QUEUE_SIZE`). Publishing is
    synchronous under the hood (``put_nowait`` only): it can never block on
    a consumer, and with zero sinks it is a no-op returning ``0``. ``logger``
    overrides the module logger (``harness.bus``) for overflow warnings.
    """

    def __init__(
        self,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        logger: logging.Logger | None = None,
    ) -> None:
        if queue_size < 1:
            raise ValueError(f"queue_size must be >= 1, got {queue_size}")
        self._queue_size = queue_size
        self._log = logger if logger is not None else _LOG
        self._sinks: dict[str, asyncio.Queue[tuple[str, dict]]] = {}
        self._overflow_counts: dict[str, int] = {}

    async def publish(self, topic: str, payload: dict) -> int:
        """Fan an event out to every subscribed sink; return delivery count.

        Validates the topic (must be in :data:`TOPICS`, else
        :class:`ValueError`) and the payload (must be a ``dict``, else
        :class:`TypeError`) before touching any sink. Each sink receives
        ``(topic, dict(payload))``; the returned count is the number of
        sinks actually enqueued to — sinks whose queue was full are dropped
        (once-per-sink warning, see module docstring) and not counted.
        """
        if topic not in TOPICS:
            raise ValueError(f"unknown topic {topic!r} (see harness.bus.TOPICS)")
        if not isinstance(payload, dict):
            raise TypeError(f"payload must be a dict, got {type(payload).__name__}")
        if not self._sinks:
            return 0
        delivered = 0
        first_overflow: list[str] = []
        for sink_name, queue in self._sinks.items():
            try:
                queue.put_nowait((topic, dict(payload)))
            except asyncio.QueueFull:
                count = self._overflow_counts.get(sink_name, 0) + 1
                self._overflow_counts[sink_name] = count
                if count == 1:
                    first_overflow.append(sink_name)
                    self._log.warning(
                        "overflow: sink=%s topic=%s dropped=%d", sink_name, topic, count
                    )
            else:
                delivered += 1
        for sink_name in first_overflow:
            self._emit_overflow_system_log(sink_name, topic)
        return delivered

    def subscribe(self, sink_name: str) -> asyncio.Queue[tuple[str, dict]]:
        """Register a fresh bounded queue under ``sink_name`` and return it.

        Sink names are arbitrary (names, not topics); a duplicate name
        raises :class:`ValueError`.
        """
        if sink_name in self._sinks:
            raise ValueError(f"sink {sink_name!r} is already subscribed")
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=self._queue_size)
        self._sinks[sink_name] = queue
        return queue

    def unsubscribe(self, sink_name: str) -> None:
        """Remove the sink named ``sink_name``; unknown names are a silent
        no-op.

        Deliberately silent (not :class:`KeyError`) so callers can
        unsubscribe defensively and the :class:`Subscriber` exit stays
        idempotent.
        """
        self._sinks.pop(sink_name, None)

    def subscriber(self, sink_name: str) -> Subscriber:
        """Return an async context manager that subscribes ``sink_name``
        on enter and unsubscribes it on exit::

            async with bus.subscriber("ui") as queue:
                topic, payload = await queue.get()
        """
        return Subscriber(self, sink_name)

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Idempotent shutdown: wait up to ``timeout_s`` for consumers to
        catch up, then unsubscribe every sink.

        Polls the sink queues until all are empty or the deadline passes
        (5 ms poll interval), then finishes via :meth:`aclose`. Leftover
        events stay with their consumer queues, which are left for the
        garbage collector.
        """
        if self._sinks and timeout_s > 0:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            while any(not queue.empty() for queue in self._sinks.values()):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.005, remaining))
        await self.aclose()

    async def aclose(self) -> None:
        """Unsubscribe all sinks immediately; idempotent (safe to call again).

        Consumer queues are neither emptied nor closed, only detached; the
        overflow counters remain available (mimicking :attr:`overflow_counts`).
        """
        self._sinks.clear()

    @property
    def sink_names(self) -> tuple[str, ...]:
        """Names of the currently subscribed sinks in subscription order."""
        return tuple(self._sinks)

    @property
    def overflow_counts(self) -> dict[str, int]:
        """Cumulative dropped-event count per sink (a defensive copy)."""
        return dict(self._overflow_counts)

    def _emit_overflow_system_log(self, sink_name: str, topic: str) -> None:
        """Best-effort ``system.log`` warn event fired on a sink's first
        overflow.

        Fully synchronous, drops silently (no counting, no warning) so the
        overflow path cannot recurse or re-warn.
        """
        payload = {
            "level": "warn",
            "msg": f"overflow: sink={sink_name} topic={topic} dropped=1",
        }
        for queue in self._sinks.values():
            try:
                queue.put_nowait(("system.log", dict(payload)))
            except asyncio.QueueFull:
                pass


class Subscriber:
    """Async context manager returned by :meth:`Bus.subscriber`.

    ``__aenter__`` subscribes a fresh sink queue (raising :class:`ValueError`
    when the name is taken) and ``__aexit__`` unsubscribes silently, so
    exiting twice is harmless.
    """

    def __init__(self, bus: Bus, sink_name: str) -> None:
        self._bus = bus
        self._sink_name = sink_name

    async def __aenter__(self) -> asyncio.Queue[tuple[str, dict]]:
        return self._bus.subscribe(self._sink_name)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bus.unsubscribe(self._sink_name)
