from __future__ import annotations

from dataclasses import dataclass
from threading import Event

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
