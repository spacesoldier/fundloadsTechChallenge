from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Event, Thread

from stream_kernel.platform.services.runtime.async_dispatch_loop import AsyncDispatchLoop


@dataclass(slots=True)
class _Payload:
    value: int


def test_async_dispatch_loop_processes_items_and_drains_on_stop() -> None:
    processed: list[int] = []
    done = Event()

    async def _handler(payload: _Payload) -> None:
        processed.append(payload.value)
        if payload.value == 3:
            done.set()

    loop = AsyncDispatchLoop(name="test.dispatch", handler=_handler, queue_max_items=8)
    loop.start()
    assert loop.submit(_Payload(1)) is True
    assert loop.submit(_Payload(2)) is True
    assert loop.submit(_Payload(3)) is True
    assert done.wait(timeout=1.0) is True
    assert loop.drain(timeout_seconds=1.0) is True
    loop.stop(drain=True, timeout_seconds=1.0)

    assert processed == [1, 2, 3]
    assert loop.metrics()["dropped"] == 0


def test_async_dispatch_loop_rejects_submit_after_stop() -> None:
    async def _handler(payload: _Payload) -> None:
        _ = payload

    loop = AsyncDispatchLoop(name="test.dispatch", handler=_handler, queue_max_items=4)
    loop.start()
    loop.stop(drain=True, timeout_seconds=1.0)
    assert loop.submit(_Payload(1)) is False


def test_async_dispatch_loop_block_with_timeout_waits_until_slot_available() -> None:
    processed: list[int] = []
    allow_first = Event()
    first_started = Event()

    async def _handler(payload: _Payload) -> None:
        processed.append(payload.value)
        if payload.value == 1:
            first_started.set()
            assert allow_first.wait(timeout=1.0) is True

    loop = AsyncDispatchLoop(
        name="test.dispatch",
        handler=_handler,
        queue_max_items=1,
        drop_policy="block_with_timeout",
        block_timeout_seconds=0.5,
    )
    loop.start()
    assert loop.submit(_Payload(1)) is True
    assert first_started.wait(timeout=1.0) is True
    assert loop.submit(_Payload(2)) is True

    def _release_after_delay() -> None:
        time.sleep(0.05)
        allow_first.set()

    releaser = Thread(target=_release_after_delay, daemon=True)
    releaser.start()
    started_at = time.perf_counter()
    assert loop.submit(_Payload(3), timeout_seconds=0.5) is True
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    assert elapsed_ms >= 30.0

    assert loop.drain(timeout_seconds=1.0) is True
    loop.stop(drain=True, timeout_seconds=1.0)
    metrics = loop.metrics()
    assert metrics["submit_block_count"] >= 1
    assert metrics["submit_timeout_count"] == 0
    assert metrics["submit_block_wait_ms_total"] >= 0
    assert processed == [1, 2, 3]


def test_async_dispatch_loop_block_with_timeout_counts_timeouts() -> None:
    allow_first = Event()
    first_started = Event()

    async def _handler(payload: _Payload) -> None:
        if payload.value == 1:
            first_started.set()
            assert allow_first.wait(timeout=1.0) is True

    loop = AsyncDispatchLoop(
        name="test.dispatch",
        handler=_handler,
        queue_max_items=1,
        drop_policy="block_with_timeout",
        block_timeout_seconds=0.05,
    )
    loop.start()
    assert loop.submit(_Payload(1)) is True
    assert first_started.wait(timeout=1.0) is True
    assert loop.submit(_Payload(2)) is True
    assert loop.submit(_Payload(3), timeout_seconds=0.05) is False

    metrics = loop.metrics()
    assert metrics["dropped"] == 1
    assert metrics["submit_timeout_count"] == 1
    assert metrics["submit_block_count"] >= 1
    assert metrics["submit_block_wait_ms_total"] > 0

    allow_first.set()
    assert loop.drain(timeout_seconds=1.0) is True
    loop.stop(drain=True, timeout_seconds=1.0)


def test_async_dispatch_loop_drop_oldest_eviction_is_deterministic() -> None:
    processed: list[int] = []
    allow_first = Event()
    first_started = Event()

    async def _handler(payload: _Payload) -> None:
        processed.append(payload.value)
        if payload.value == 1:
            first_started.set()
            assert allow_first.wait(timeout=1.0) is True

    loop = AsyncDispatchLoop(
        name="test.dispatch",
        handler=_handler,
        queue_max_items=1,
        drop_policy="drop_oldest",
    )
    loop.start()
    assert loop.submit(_Payload(1)) is True
    assert first_started.wait(timeout=1.0) is True
    assert loop.submit(_Payload(2)) is True
    assert loop.submit(_Payload(3)) is True
    allow_first.set()
    assert loop.drain(timeout_seconds=1.0) is True
    loop.stop(drain=True, timeout_seconds=1.0)

    metrics = loop.metrics()
    assert metrics["dropped"] == 1
    assert processed == [1, 3]


def test_async_dispatch_loop_block_forever_waits_until_slot_available() -> None:
    processed: list[int] = []
    allow_first = Event()
    first_started = Event()

    async def _handler(payload: _Payload) -> None:
        processed.append(payload.value)
        if payload.value == 1:
            first_started.set()
            assert allow_first.wait(timeout=1.0) is True

    loop = AsyncDispatchLoop(
        name="test.dispatch",
        handler=_handler,
        queue_max_items=1,
        drop_policy="block_forever",
    )
    loop.start()
    assert loop.submit(_Payload(1)) is True
    assert first_started.wait(timeout=1.0) is True
    assert loop.submit(_Payload(2)) is True

    def _release_after_delay() -> None:
        time.sleep(0.05)
        allow_first.set()

    releaser = Thread(target=_release_after_delay, daemon=True)
    releaser.start()
    started_at = time.perf_counter()
    assert loop.submit(_Payload(3)) is True
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    assert elapsed_ms >= 30.0

    assert loop.drain(timeout_seconds=1.0) is True
    loop.stop(drain=True, timeout_seconds=1.0)
    metrics = loop.metrics()
    assert metrics["submit_block_count"] >= 1
    assert metrics["submit_timeout_count"] == 0
    assert metrics["dropped"] == 0
    assert processed == [1, 2, 3]
