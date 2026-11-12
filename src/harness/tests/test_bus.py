"""Tests for harness.bus (ARCHITECTURE.md section 2.3 pub/sub contract)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from harness.bus import TOPICS, Bus, Subscriber


def _chunk(seq: int) -> dict:
    return {"session_id": "sess-1", "seq": seq, "text": f"chunk-{seq}", "source": "agent"}


def _drain(queue: asyncio.Queue) -> list[tuple[str, dict]]:
    drained: list[tuple[str, dict]] = []
    try:
        while True:
            drained.append(queue.get_nowait())
    except asyncio.QueueEmpty:
        pass
    return drained


async def test_topics_registry_exact() -> None:
    assert set(TOPICS) == {
        "user.msg",
        "route.decided",
        "task.appeared",
        "task.state",
        "gate.opened",
        "gate.answered",
        "gate.notify",
        "schedule.fired",
        "agent.event",
        "agent.done",
        "session.status",
        "reply.chunk",
        "reply.final",
        "system.log",
        "handoff.request",
        "memory.updated",
        "skill.captured",
    }
    assert len(TOPICS) == 17
    assert all(isinstance(keys, tuple) and keys for keys in TOPICS.values())


async def test_publish_unknown_topic_raises_and_fans_out_nothing() -> None:
    bus = Bus()
    queue = bus.subscribe("ui")
    with pytest.raises(ValueError, match="nope.topic"):
        await bus.publish("nope.topic", {"level": "info", "msg": "hi"})
    assert queue.empty()


async def test_publish_payload_must_be_dict() -> None:
    bus = Bus()
    queue = bus.subscribe("ui")
    with pytest.raises(TypeError):
        await bus.publish("system.log", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert queue.empty()


async def test_subscribe_duplicate_name_rejected() -> None:
    bus = Bus()
    bus.subscribe("ui")
    with pytest.raises(ValueError, match="already"):
        bus.subscribe("ui")
    assert bus.sink_names == ("ui",)


async def test_fanout_exactly_once_in_order_all_sinks() -> None:
    bus = Bus()
    queues = [bus.subscribe(f"sink-{i}") for i in range(3)]
    expected: list[tuple[str, dict]] = []
    for seq in range(5):
        payload = _chunk(seq)
        expected.append(("reply.chunk", payload))
        assert await bus.publish("reply.chunk", payload) == 3
    for queue in queues:
        received = [queue.get_nowait() for _ in range(5)]
        assert received == expected
        assert queue.empty()


async def test_publish_returns_delivered_count() -> None:
    bus = Bus()
    payload = {"level": "info", "msg": "hi"}
    assert await bus.publish("system.log", payload) == 0
    bus.subscribe("a")
    assert await bus.publish("system.log", dict(payload)) == 1
    bus.subscribe("b")
    assert await bus.publish("system.log", dict(payload)) == 2
    bus.unsubscribe("b")
    assert await bus.publish("system.log", dict(payload)) == 1
    assert bus.sink_names == ("a",)


async def test_payload_copied_per_sink() -> None:
    bus = Bus()
    sink_a = bus.subscribe("a")
    sink_b = bus.subscribe("b")
    payload = {"level": "info", "msg": "original"}
    assert await bus.publish("system.log", payload) == 2
    payload["msg"] = "mutated-after-publish"
    _, got_a = sink_a.get_nowait()
    _, got_b = sink_b.get_nowait()
    assert got_a == {"level": "info", "msg": "original"}
    assert got_b == {"level": "info", "msg": "original"}
    got_a["msg"] = "mutated-by-consumer-a"
    assert got_b["msg"] == "original"


async def test_overflow_drop_and_warn_once(caplog: pytest.LogCaptureFixture) -> None:
    bus = Bus(queue_size=4)
    slow = bus.subscribe("slow")
    fast = bus.subscribe("fast")
    seen_by_fast: list[dict] = []
    system_log_events: list[dict] = []
    with caplog.at_level(logging.WARNING, logger="harness.bus"):
        for seq in range(10):
            delivered = await bus.publish("reply.chunk", _chunk(seq))
            if seq < 4:
                assert delivered == 2
            else:
                assert delivered == 1
            for event_topic, payload in _drain(fast):
                if event_topic == "reply.chunk":
                    seen_by_fast.append(payload)
                else:
                    assert event_topic == "system.log"
                    system_log_events.append(payload)
    assert [payload["seq"] for payload in seen_by_fast] == list(range(10))
    assert [payload["seq"] for _, payload in _drain(slow)] == [0, 1, 2, 3]
    assert slow.empty()
    assert system_log_events == [
        {"level": "warn", "msg": "overflow: sink=slow topic=reply.chunk dropped=1"}
    ]
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "overflow" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == "overflow: sink=slow topic=reply.chunk dropped=1"
    counts = bus.overflow_counts
    assert counts["slow"] == 6 and "fast" not in counts
    counts["slow"] = 999
    assert bus.overflow_counts["slow"] == 6


async def test_full_sink_does_not_block_publisher() -> None:
    bus = Bus(queue_size=2)
    bus.subscribe("stalled")
    consumer = bus.subscribe("consumer")
    seen: list[dict] = []
    loop = asyncio.get_running_loop()
    start = loop.time()
    for seq in range(30):
        await asyncio.wait_for(
            bus.publish("reply.chunk", _chunk(seq)),
            timeout=0.1,
        )
        seen.extend(
            payload for event_topic, payload in _drain(consumer) if event_topic == "reply.chunk"
        )
    elapsed = loop.time() - start
    assert elapsed < 0.5
    assert [payload["seq"] for payload in seen] == list(range(30))
    assert bus.overflow_counts["stalled"] == 28


async def test_subscriber_context_manager_subscribes_and_unsubscribes() -> None:
    bus = Bus()
    cm = bus.subscriber("ui")
    assert isinstance(cm, Subscriber)
    async with cm as queue:
        assert bus.sink_names == ("ui",)
        await bus.publish("gate.opened", {"task_id": "t1", "question": "go?"})
        topic, payload = queue.get_nowait()
        assert topic == "gate.opened"
        assert payload == {"task_id": "t1", "question": "go?"}
    assert bus.sink_names == ()
    assert await bus.publish("gate.opened", {"task_id": "t2", "question": "again?"}) == 0
    assert queue.empty()


async def test_unsubscribe_unknown_is_silent_noop() -> None:
    bus = Bus()
    bus.unsubscribe("ghost")
    assert bus.sink_names == ()
    queue = bus.subscribe("x")
    bus.unsubscribe("x")
    bus.unsubscribe("x")
    assert bus.sink_names == ()
    assert await bus.publish("system.log", {"level": "info", "msg": "hi"}) == 0
    assert queue.empty()


async def test_drain_and_aclose_idempotent() -> None:
    bus = Bus(queue_size=8)
    bus.subscribe("a")
    queue_b = bus.subscribe("b")
    assert await bus.publish("system.log", {"level": "info", "msg": "hi"}) == 2
    assert bus.sink_names == ("a", "b")
    await bus.drain(timeout_s=0.01)
    assert bus.sink_names == ()
    assert queue_b.qsize() == 1
    await bus.drain()
    await bus.aclose()
    await bus.aclose()
    assert bus.sink_names == ()
    assert await bus.publish("system.log", {"level": "info", "msg": "post"}) == 0


async def test_drain_returns_promptly_when_queues_empty() -> None:
    bus = Bus()
    bus.subscribe("a")
    loop = asyncio.get_running_loop()
    start = loop.time()
    await bus.drain(timeout_s=5.0)
    assert loop.time() - start < 0.2
    assert bus.sink_names == ()
