from __future__ import annotations

import asyncio
import inspect
import queue
import time
from collections.abc import Awaitable, Callable
from threading import Event, Lock, Thread
from typing import Generic, TypeVar

TItem = TypeVar("TItem")
_SENTINEL = object()
_SUPPORTED_DROP_POLICIES = {"drop_newest", "drop_oldest", "block_with_timeout"}


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
        drop_policy: str = "drop_newest",
        block_timeout_seconds: float = 0.1,
    ) -> None:
        if queue_max_items <= 0:
            raise ValueError("AsyncDispatchLoop queue_max_items must be > 0")
        if drop_policy not in _SUPPORTED_DROP_POLICIES:
            raise ValueError(f"AsyncDispatchLoop drop_policy must be one of: {sorted(_SUPPORTED_DROP_POLICIES)}")
        if block_timeout_seconds <= 0:
            raise ValueError("AsyncDispatchLoop block_timeout_seconds must be > 0")
        self._name = name
        self._handler = handler
        self._queue: queue.Queue[object] = queue.Queue(maxsize=queue_max_items)
        self._drop_policy = drop_policy
        self._block_timeout_seconds = float(block_timeout_seconds)
        self._thread: Thread | None = None
        self._ready = Event()
        self._state_lock = Lock()
        self._running = False
        self._stopping = False
        self._submitted = 0
        self._processed = 0
        self._failed = 0
        self._dropped = 0
        self._submit_block_count = 0
        self._submit_timeout_count = 0
        self._submit_block_wait_ms_total = 0

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
        with self._state_lock:
            if not self._running or self._stopping:
                return False
        if self._drop_policy == "drop_newest":
            return self._submit_drop_newest(item)
        if self._drop_policy == "drop_oldest":
            return self._submit_drop_oldest(item)
        return self._submit_block_with_timeout(item, timeout_seconds=timeout_seconds)

    def drain(self, *, timeout_seconds: float = 2.0) -> bool:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        drained = Event()
        try:
            self._queue.put(drained, timeout=max(0.01, timeout_seconds))
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

    def metrics(self) -> dict[str, object]:
        queue_depth = self._queue.qsize()
        pending = max(0, self._submitted - self._processed - self._failed)
        with self._state_lock:
            return {
                "submitted": self._submitted,
                "processed": self._processed,
                "failed": self._failed,
                "dropped": self._dropped,
                "queue_depth": queue_depth,
                "pending": pending,
                "running": 1 if self._running else 0,
                "queue_max_items": self._queue.maxsize,
                "drop_policy": self._drop_policy,
                "submit_block_count": self._submit_block_count,
                "submit_timeout_count": self._submit_timeout_count,
                "submit_block_wait_ms_total": self._submit_block_wait_ms_total,
            }

    def _submit_drop_newest(self, item: TItem) -> bool:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._state_lock:
                self._dropped += 1
            return False
        with self._state_lock:
            self._submitted += 1
        return True

    def _submit_drop_oldest(self, item: TItem) -> bool:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                oldest = self._queue.get_nowait()
            except queue.Empty:
                with self._state_lock:
                    self._dropped += 1
                return False
            if oldest is _SENTINEL or isinstance(oldest, Event):
                try:
                    self._queue.put_nowait(oldest)
                except queue.Full:
                    pass
                with self._state_lock:
                    self._dropped += 1
                return False
            self._queue.task_done()
            with self._state_lock:
                self._dropped += 1
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                with self._state_lock:
                    self._dropped += 1
                return False
        with self._state_lock:
            self._submitted += 1
        return True

    def _submit_block_with_timeout(self, item: TItem, *, timeout_seconds: float) -> bool:
        timeout = timeout_seconds if timeout_seconds > 0 else self._block_timeout_seconds
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            started = time.perf_counter()
            with self._state_lock:
                self._submit_block_count += 1
            try:
                self._queue.put(item, timeout=timeout)
            except queue.Full:
                waited_ms = int((time.perf_counter() - started) * 1000)
                with self._state_lock:
                    self._submit_timeout_count += 1
                    self._submit_block_wait_ms_total += max(0, waited_ms)
                    self._dropped += 1
                return False
            waited_ms = int((time.perf_counter() - started) * 1000)
            with self._state_lock:
                self._submit_block_wait_ms_total += max(0, waited_ms)
        with self._state_lock:
            self._submitted += 1
        return True

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
