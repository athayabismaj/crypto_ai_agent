import asyncio

import pytest  # type: ignore

from runtime.agent.core.event_bus import EventBus, EventType  # type: ignore


@pytest.mark.asyncio
async def test_event_bus_pub_sub():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event.payload)

    def sync_handler(event):
        received.append("sync_" + event.payload)

    bus.subscribe(EventType.CANDLE_READY, handler)
    bus.subscribe(EventType.CANDLE_READY, sync_handler)

    bus.start()
    bus.publish(EventType.CANDLE_READY, "data1", "test")

    await asyncio.sleep(0.1)  # Wait for dispatch
    bus.stop()
    await asyncio.sleep(0.01)

    assert len(received) == 2
    assert "data1" in received
    assert "sync_data1" in received


@pytest.mark.asyncio
async def test_event_bus_queue_full():
    bus = EventBus()
    bus._queue = asyncio.Queue(maxsize=1)

    bus.publish(EventType.CANDLE_READY, "1", "t")
    # Queue is now full, next publish should log error but not crash
    bus.publish(EventType.CANDLE_READY, "2", "t")

    assert bus._queue.qsize() == 1


@pytest.mark.asyncio
async def test_event_bus_handler_error():
    bus = EventBus()

    def bad_handler(event):
        raise ValueError("Simulated handler error")

    bus.subscribe(EventType.CANDLE_READY, bad_handler)

    bus.start()
    bus.publish(EventType.CANDLE_READY, "data1", "t")
    await asyncio.sleep(0.1)
    bus.stop()
    await asyncio.sleep(0.01)
    # It passes if the loop doesn't crash
