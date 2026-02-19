from __future__ import annotations

import asyncio
import inspect
import queue
from collections.abc import Awaitable, Callable
from threading import Event, Lock, Thread
from typing import Generic, TypeVar

TItem = TypeVar("TItem")
_SENTINEL = object()


class AsyncDispatchLoop(Generic[TItem]):
    # Reusable non-blocking dispatch worker:
    # - sync submit on caller thread;
    # - async-capable handler executed on dedicated worker thread.
    def __init__(
        self,
        *,
        name: str,
        handler: Callable[[TItem], Awaitable[None] | None],
        queue_max_items: int = 2048,
    ) -> None:
        if queue_max_items <= 0:
            raise ValueError("AsyncDispatchLoop queue_max_items must be > 0")
        self._name = name
        self._handler = handler
        self._queue: queue.Queue[object] = queue.Queue(maxsize=queue_max_items)
        self._thread: Thread | None = None
        self._ready = Event()
        self._state_lock = Lock()
        self._running = False
        self._stopping = False
        self._submitted = 0
        self._processed = 0
        self._failed = 0
        self._dropped = 0

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._stopping = False
            self._ready.clear()
            self._thread = Thread(target=self._thread_main, name=self._name, daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=1.0):
            raise RuntimeError("AsyncDispatchLoop failed to start worker thread")
        with self._state_lock:
            self._running = True

    def submit(self, item: TItem, *, timeout_seconds: float = 0.2) -> bool:
        _ = timeout_seconds
        with self._state_lock:
            if not self._running or self._stopping:
                return False
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._state_lock:
                self._dropped += 1
            return False
        with self._state_lock:
            self._submitted += 1
        return True

    def drain(self, *, timeout_seconds: float = 2.0) -> bool:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        drained = Event()
        try:
            self._queue.put_nowait(drained)
        except queue.Full:
            return False
        return drained.wait(timeout=max(0.01, timeout_seconds))

    def stop(self, *, drain: bool = True, timeout_seconds: float = 2.0) -> None:
        with self._state_lock:
            if not self._running and self._stopping:
                return
            self._stopping = True

        if drain:
            _ = self.drain(timeout_seconds=max(0.01, timeout_seconds))

        thread = self._thread
        if thread is None:
            with self._state_lock:
                self._running = False
            return
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                self._queue.put(_SENTINEL, timeout=max(0.01, timeout_seconds))
            except Exception:
                pass
        thread.join(timeout=max(0.01, timeout_seconds))
        with self._state_lock:
            self._running = False
            self._thread = None

    def metrics(self) -> dict[str, int]:
        with self._state_lock:
            return {
                "submitted": self._submitted,
                "processed": self._processed,
                "failed": self._failed,
                "dropped": self._dropped,
                "running": 1 if self._running else 0,
            }

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    self._queue.task_done()
                    break
                if isinstance(item, Event):
                    self._queue.task_done()
                    item.set()
                    continue
                try:
                    result = self._handler(item)  # type: ignore[arg-type]
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
                    with self._state_lock:
                        self._processed += 1
                except Exception:
                    with self._state_lock:
                        self._failed += 1
                finally:
                    self._queue.task_done()
        finally:
            try:
                loop.close()
            except Exception:
                pass
