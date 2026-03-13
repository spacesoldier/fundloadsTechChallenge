from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.work_queue import QueuePort
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.routing.envelope import Envelope

_JOBS_KEY = "platform.scheduler.jobs"
_TIMER_JOBS_KEY = "platform.scheduler.timer.jobs"


class PlatformSchedulerStore(KVStore):
    # KV marker contract for scheduler job persistence.
    pass


class PlatformSchedulerTimerStore(KVStore):
    # KV marker contract for async timer job persistence.
    pass


@dataclass(frozen=True, slots=True)
class PlatformSchedulerUpsertCommand:
    job_id: str
    target: str
    interval_seconds: float
    run_immediately: bool = True
    payload: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("PlatformSchedulerUpsertCommand.job_id must be a non-empty string")
        if not isinstance(self.target, str) or not self.target:
            raise ValueError("PlatformSchedulerUpsertCommand.target must be a non-empty string")
        if not isinstance(self.interval_seconds, (int, float)) or float(self.interval_seconds) <= 0:
            raise ValueError("PlatformSchedulerUpsertCommand.interval_seconds must be > 0")
        if not isinstance(self.run_immediately, bool):
            raise ValueError("PlatformSchedulerUpsertCommand.run_immediately must be a boolean")


@dataclass(frozen=True, slots=True)
class PlatformSchedulerCancelCommand:
    job_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("PlatformSchedulerCancelCommand.job_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class PlatformSchedulerTickEvent:
    issued_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True, slots=True)
class PlatformSchedulerDispatch:
    job_id: str
    target: str
    payload: object


@dataclass(frozen=True, slots=True)
class PlatformSchedulerJobSnapshot:
    job_id: str
    target: str
    interval_seconds: float
    next_due_monotonic: float
    last_dispatched_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class PlatformSchedulerSnapshot:
    jobs: tuple[PlatformSchedulerJobSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class PlatformSchedulerTimerJobSnapshot:
    job_id: str
    target: str
    interval_seconds: float
    run_immediately: bool
    last_tick_epoch_ms: int | None = None
    active: bool = False


@dataclass(frozen=True, slots=True)
class PlatformSchedulerTimerSnapshot:
    jobs: tuple[PlatformSchedulerTimerJobSnapshot, ...] = ()


@runtime_checkable
class PlatformSchedulerService(Protocol):
    def apply_command(
        self,
        command: PlatformSchedulerUpsertCommand | PlatformSchedulerCancelCommand,
        *,
        now_monotonic: float | None = None,
    ) -> None:
        raise NotImplementedError

    def dispatch_due(
        self,
        tick: PlatformSchedulerTickEvent | None = None,
        *,
        now_monotonic: float | None = None,
    ) -> list[PlatformSchedulerDispatch]:
        raise NotImplementedError

    def snapshot(self) -> PlatformSchedulerSnapshot:
        raise NotImplementedError


@runtime_checkable
class PlatformSchedulerTickerService(Protocol):
    def ensure_started(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


@runtime_checkable
class PlatformSchedulerTimerService(Protocol):
    def apply_command(
        self,
        command: PlatformSchedulerUpsertCommand | PlatformSchedulerCancelCommand,
    ) -> None:
        raise NotImplementedError

    def snapshot(self) -> PlatformSchedulerTimerSnapshot:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@service(name="platform_scheduler_service")
@dataclass(slots=True)
class InMemoryPlatformSchedulerService(PlatformSchedulerService):
    store: KVStore = inject.kv(PlatformSchedulerStore)

    def apply_command(
        self,
        command: PlatformSchedulerUpsertCommand | PlatformSchedulerCancelCommand,
        *,
        now_monotonic: float | None = None,
    ) -> None:
        jobs = self._load_jobs()
        if isinstance(command, PlatformSchedulerUpsertCommand):
            now = _resolve_now_monotonic(now_monotonic)
            interval = max(0.001, float(command.interval_seconds))
            next_due = now if command.run_immediately else now + interval
            jobs[command.job_id] = {
                "job_id": command.job_id,
                "target": command.target,
                "interval_seconds": interval,
                "next_due_monotonic": next_due,
                "last_dispatched_monotonic": None,
                "payload": command.payload,
            }
            self._save_jobs(jobs)
            return
        if isinstance(command, PlatformSchedulerCancelCommand):
            if command.job_id in jobs:
                del jobs[command.job_id]
                self._save_jobs(jobs)

    def dispatch_due(
        self,
        tick: PlatformSchedulerTickEvent | None = None,
        *,
        now_monotonic: float | None = None,
    ) -> list[PlatformSchedulerDispatch]:
        _ = tick
        now = _resolve_now_monotonic(now_monotonic)
        jobs = self._load_jobs()
        if not jobs:
            return []
        due: list[PlatformSchedulerDispatch] = []
        changed = False
        for job_id in sorted(jobs):
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            target = job.get("target")
            if not isinstance(target, str) or not target:
                continue
            interval = _as_positive_float(job.get("interval_seconds"), default=0.001)
            next_due = _as_float(job.get("next_due_monotonic"), default=now + interval)
            if now < next_due:
                continue
            payload = job.get("payload")
            due.append(
                PlatformSchedulerDispatch(
                    job_id=job_id,
                    target=target,
                    payload=payload,
                )
            )
            job["last_dispatched_monotonic"] = now
            job["next_due_monotonic"] = now + interval
            changed = True
        if changed:
            self._save_jobs(jobs)
        return due

    def snapshot(self) -> PlatformSchedulerSnapshot:
        jobs = self._load_jobs()
        snapshots = []
        for job_id in sorted(jobs):
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            target = job.get("target")
            if not isinstance(target, str) or not target:
                continue
            snapshots.append(
                PlatformSchedulerJobSnapshot(
                    job_id=job_id,
                    target=target,
                    interval_seconds=_as_positive_float(job.get("interval_seconds"), default=0.001),
                    next_due_monotonic=_as_float(job.get("next_due_monotonic"), default=0.0),
                    last_dispatched_monotonic=_as_optional_float(
                        job.get("last_dispatched_monotonic")
                    ),
                )
            )
        return PlatformSchedulerSnapshot(jobs=tuple(snapshots))

    def _load_jobs(self) -> dict[str, dict[str, object]]:
        raw = self.store.get(_JOBS_KEY)
        if not isinstance(raw, dict):
            return {}
        loaded: dict[str, dict[str, object]] = {}
        for job_id, value in raw.items():
            if not isinstance(job_id, str) or not job_id:
                continue
            if not isinstance(value, dict):
                continue
            loaded[job_id] = dict(value)
        return loaded

    def _save_jobs(self, jobs: dict[str, dict[str, object]]) -> None:
        self.store.set(_JOBS_KEY, {job_id: dict(value) for job_id, value in jobs.items()})


@service(name="platform_scheduler_timer_service")
@dataclass(slots=True)
class AsyncioPlatformSchedulerTimerService(PlatformSchedulerTimerService):
    store: KVStore = inject.kv(PlatformSchedulerTimerStore)
    work_queue: object = inject.queue(Envelope, qualifier="execution.asyncio")
    tick_target: str = "system.scheduler.tick"
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)

    def apply_command(
        self,
        command: PlatformSchedulerUpsertCommand | PlatformSchedulerCancelCommand,
    ) -> None:
        if isinstance(command, PlatformSchedulerUpsertCommand):
            self._upsert(command)
            return
        if isinstance(command, PlatformSchedulerCancelCommand):
            self._cancel(command.job_id)

    def snapshot(self) -> PlatformSchedulerTimerSnapshot:
        jobs = self._load_jobs()
        snapshots: list[PlatformSchedulerTimerJobSnapshot] = []
        for job_id in sorted(jobs):
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            target = job.get("target")
            if not isinstance(target, str) or not target:
                continue
            snapshots.append(
                PlatformSchedulerTimerJobSnapshot(
                    job_id=job_id,
                    target=target,
                    interval_seconds=_as_positive_float(job.get("interval_seconds"), default=0.001),
                    run_immediately=bool(job.get("run_immediately", True)),
                    last_tick_epoch_ms=(
                        int(job.get("last_tick_epoch_ms"))
                        if isinstance(job.get("last_tick_epoch_ms"), int)
                        else None
                    ),
                    active=self._is_task_active(job_id),
                )
            )
        return PlatformSchedulerTimerSnapshot(jobs=tuple(snapshots))

    def close(self) -> None:
        for job_id in list(self._tasks.keys()):
            self._cancel_task(job_id)

    def _upsert(self, command: PlatformSchedulerUpsertCommand) -> None:
        interval = max(0.001, float(command.interval_seconds))
        jobs = self._load_jobs()
        jobs[command.job_id] = {
            "job_id": command.job_id,
            "target": command.target,
            "interval_seconds": interval,
            "run_immediately": bool(command.run_immediately),
            "payload": command.payload,
            "last_tick_epoch_ms": jobs.get(command.job_id, {}).get("last_tick_epoch_ms")
            if isinstance(jobs.get(command.job_id), dict)
            else None,
        }
        self._save_jobs(jobs)
        self._arm_task(
            job_id=command.job_id,
            interval_seconds=interval,
            run_immediately=bool(command.run_immediately),
        )

    def _cancel(self, job_id: str) -> None:
        jobs = self._load_jobs()
        if job_id in jobs:
            del jobs[job_id]
            self._save_jobs(jobs)
        self._cancel_task(job_id)

    def _arm_task(
        self,
        *,
        job_id: str,
        interval_seconds: float,
        run_immediately: bool,
    ) -> None:
        self._cancel_task(job_id)
        loop = self._running_loop()
        if loop is None:
            return
        task = loop.create_task(
            self._timer_loop(
                job_id=job_id,
                interval_seconds=max(0.001, float(interval_seconds)),
                run_immediately=bool(run_immediately),
            ),
            name=f"platform-scheduler-timer:{job_id}",
        )
        self._tasks[job_id] = task

    async def _timer_loop(
        self,
        *,
        job_id: str,
        interval_seconds: float,
        run_immediately: bool,
    ) -> None:
        delay = 0.0 if run_immediately else max(0.001, float(interval_seconds))
        try:
            while True:
                if delay > 0:
                    await asyncio.sleep(delay)
                queue = self._queue_port()
                if queue is not None:
                    try:
                        queue.push(
                            Envelope(
                                payload=PlatformSchedulerTickEvent(),
                                target=self.tick_target,
                            )
                        )
                    except Exception:
                        pass
                self._mark_tick(job_id)
                delay = max(0.001, float(interval_seconds))
        except asyncio.CancelledError:
            return
        finally:
            current = self._tasks.get(job_id)
            if current is not None and current.done():
                self._tasks.pop(job_id, None)

    def _mark_tick(self, job_id: str) -> None:
        jobs = self._load_jobs()
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            return
        job["last_tick_epoch_ms"] = int(time.time() * 1000)
        self._save_jobs(jobs)

    def _cancel_task(self, job_id: str) -> None:
        task = self._tasks.pop(job_id, None)
        if task is None:
            return
        if not task.done():
            task.cancel()

    def _is_task_active(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return bool(task is not None and not task.done())

    def _running_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _queue_port(self) -> QueuePort | None:
        candidate = self.work_queue
        if isinstance(candidate, QueuePort):
            return candidate
        if callable(getattr(candidate, "push", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _load_jobs(self) -> dict[str, dict[str, object]]:
        raw = self.store.get(_TIMER_JOBS_KEY)
        if not isinstance(raw, dict):
            return {}
        loaded: dict[str, dict[str, object]] = {}
        for job_id, value in raw.items():
            if not isinstance(job_id, str) or not job_id:
                continue
            if not isinstance(value, dict):
                continue
            loaded[job_id] = dict(value)
        return loaded

    def _save_jobs(self, jobs: dict[str, dict[str, object]]) -> None:
        self.store.set(_TIMER_JOBS_KEY, {job_id: dict(value) for job_id, value in jobs.items()})


@service(name="platform_scheduler_ticker_service")
@dataclass(slots=True)
class DefaultPlatformSchedulerTickerService(PlatformSchedulerTickerService):
    scheduler: PlatformSchedulerService = inject.service(PlatformSchedulerService)
    work_queue: object = inject.queue(Envelope, qualifier="execution.asyncio")
    tick_target: str = "system.scheduler.tick"
    tick_interval_seconds: float = 0.01
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._thread_main,
                name="platform-scheduler-ticker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=1.0)

    def _thread_main(self) -> None:
        interval = max(0.001, float(self.tick_interval_seconds))
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._ticker_loop(interval_seconds=interval))
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _ticker_loop(self, *, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            queue = self._queue_port()
            scheduler = self._scheduler()
            if queue is not None and scheduler is not None and self._has_jobs(scheduler):
                try:
                    queue.push(
                        Envelope(
                            payload=PlatformSchedulerTickEvent(),
                            target=self.tick_target,
                        )
                    )
                except Exception:
                    pass
            await asyncio.sleep(max(0.001, float(interval_seconds)))

    def _queue_port(self) -> QueuePort | None:
        candidate = self.work_queue
        if isinstance(candidate, QueuePort):
            return candidate
        if callable(getattr(candidate, "push", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _scheduler(self) -> PlatformSchedulerService | None:
        candidate = self.scheduler
        if isinstance(candidate, PlatformSchedulerService):
            return candidate
        if callable(getattr(candidate, "snapshot", None)):
            return candidate  # type: ignore[return-value]
        return None

    @staticmethod
    def _has_jobs(scheduler: PlatformSchedulerService) -> bool:
        try:
            snapshot = scheduler.snapshot()
        except Exception:
            return False
        if not isinstance(snapshot, PlatformSchedulerSnapshot):
            return False
        return bool(snapshot.jobs)


def _resolve_now_monotonic(now_monotonic: float | None) -> float:
    if isinstance(now_monotonic, (int, float)):
        return float(now_monotonic)
    return time.monotonic()


def _as_float(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(default)


def _as_optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_positive_float(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return float(default)


__all__ = [
    "PlatformSchedulerStore",
    "PlatformSchedulerTimerStore",
    "PlatformSchedulerUpsertCommand",
    "PlatformSchedulerCancelCommand",
    "PlatformSchedulerTickEvent",
    "PlatformSchedulerDispatch",
    "PlatformSchedulerJobSnapshot",
    "PlatformSchedulerSnapshot",
    "PlatformSchedulerTimerJobSnapshot",
    "PlatformSchedulerTimerSnapshot",
    "PlatformSchedulerService",
    "PlatformSchedulerTimerService",
    "AsyncioPlatformSchedulerTimerService",
    "PlatformSchedulerTickerService",
    "DefaultPlatformSchedulerTickerService",
    "InMemoryPlatformSchedulerService",
]
