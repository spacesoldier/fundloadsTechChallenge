from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
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
    max_dispatch_per_tick: int = 256
    lane_weights: dict[str, int] = field(
        default_factory=lambda: {
            "control": 4,
            "data": 8,
            "trace": 2,
            "log": 1,
            "metric": 1,
        }
    )

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
        due_by_lane: dict[str, list[tuple[str, str, object, float]]] = {
            "control": [],
            "data": [],
            "trace": [],
            "log": [],
            "metric": [],
        }
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
            lane = _scheduler_lane_for_target(target)
            if lane not in due_by_lane:
                lane = "control"
            due_by_lane[lane].append((job_id, target, payload, interval))
        budget = _as_positive_int(self.max_dispatch_per_tick, default=256)
        selected = _select_weighted_due(
            due_by_lane=due_by_lane,
            budget=budget,
            lane_weights=self.lane_weights,
        )
        due: list[PlatformSchedulerDispatch] = []
        for job_id, target, payload, interval in selected:
            due.append(
                PlatformSchedulerDispatch(
                    job_id=job_id,
                    target=target,
                    payload=payload,
                )
            )
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            job["last_dispatched_monotonic"] = now
            job["next_due_monotonic"] = now + interval
        if due:
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
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _wake_event: asyncio.Event | None = field(default=None, init=False, repr=False)

    def apply_command(
        self,
        command: PlatformSchedulerUpsertCommand | PlatformSchedulerCancelCommand,
    ) -> None:
        if isinstance(command, PlatformSchedulerUpsertCommand):
            self._upsert(command)
        elif isinstance(command, PlatformSchedulerCancelCommand):
            self._cancel(command.job_id)
        self._ensure_task()
        self._wake_loop()

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
        self._cancel_task()

    def _upsert(self, command: PlatformSchedulerUpsertCommand) -> None:
        now_ms = int(time.time() * 1000)
        interval = max(0.001, float(command.interval_seconds))
        interval_ms = max(1, int(interval * 1000))
        jobs = self._load_jobs()
        previous = jobs.get(command.job_id) if isinstance(jobs.get(command.job_id), dict) else {}
        next_tick_epoch_ms = now_ms if bool(command.run_immediately) else now_ms + interval_ms
        jobs[command.job_id] = {
            "job_id": command.job_id,
            "target": command.target,
            "interval_seconds": interval,
            "run_immediately": bool(command.run_immediately),
            "payload": command.payload,
            "last_tick_epoch_ms": previous.get("last_tick_epoch_ms"),
            "next_tick_epoch_ms": next_tick_epoch_ms,
        }
        self._save_jobs(jobs)

    def _cancel(self, job_id: str) -> None:
        jobs = self._load_jobs()
        if job_id in jobs:
            del jobs[job_id]
            self._save_jobs(jobs)

    async def _timer_loop(self) -> None:
        try:
            while True:
                jobs = self._load_jobs()
                if not jobs:
                    await self._wait_for_wake(None)
                    continue
                now_ms = int(time.time() * 1000)
                next_due_ms = self._next_due_epoch_ms(jobs=jobs, now_ms=now_ms)
                delay_seconds = max(0.0, float(next_due_ms - now_ms) / 1000.0)
                woke = await self._wait_for_wake(delay_seconds)
                if woke:
                    continue
                jobs = self._load_jobs()
                if not jobs:
                    continue
                now_ms = int(time.time() * 1000)
                due_job_ids = self._due_job_ids(jobs=jobs, now_ms=now_ms)
                if not due_job_ids:
                    continue
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
                self._mark_tick(jobs=jobs, due_job_ids=due_job_ids, now_ms=now_ms)
                self._save_jobs(jobs)
        except asyncio.CancelledError:
            return
        finally:
            self._task = None

    def _mark_tick(
        self,
        *,
        jobs: dict[str, dict[str, object]],
        due_job_ids: list[str],
        now_ms: int,
    ) -> None:
        for job_id in due_job_ids:
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            interval_seconds = _as_positive_float(job.get("interval_seconds"), default=0.001)
            interval_ms = max(1, int(interval_seconds * 1000))
            job["last_tick_epoch_ms"] = now_ms
            job["next_tick_epoch_ms"] = now_ms + interval_ms

    def _cancel_task(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        if not task.done():
            task.cancel()

    def _is_task_active(self, _job_id: str) -> bool:
        task = self._task
        return bool(task is not None and not task.done())

    def _ensure_task(self) -> None:
        task = self._task
        if task is not None and not task.done():
            return
        loop = self._running_loop()
        if loop is None:
            return
        if self._wake_event is None:
            self._wake_event = asyncio.Event()
        self._task = loop.create_task(
            self._timer_loop(),
            name="platform-scheduler-timer",
        )

    def _wake_loop(self) -> None:
        event = self._wake_event
        if event is None:
            return
        if not event.is_set():
            event.set()

    async def _wait_for_wake(self, delay_seconds: float | None) -> bool:
        event = self._wake_event
        if event is None:
            if delay_seconds is None:
                await asyncio.sleep(0.001)
                return True
            await asyncio.sleep(max(0.0, float(delay_seconds)))
            return False
        if event.is_set():
            event.clear()
            return True
        if delay_seconds is None:
            await event.wait()
            event.clear()
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.0, float(delay_seconds)))
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _next_due_epoch_ms(*, jobs: dict[str, dict[str, object]], now_ms: int) -> int:
        next_due: int | None = None
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            candidate = job.get("next_tick_epoch_ms")
            if not isinstance(candidate, int):
                interval_seconds = _as_positive_float(job.get("interval_seconds"), default=0.001)
                candidate = now_ms + max(1, int(interval_seconds * 1000))
            if next_due is None or candidate < next_due:
                next_due = candidate
        if next_due is None:
            return now_ms
        return int(next_due)

    @staticmethod
    def _due_job_ids(*, jobs: dict[str, dict[str, object]], now_ms: int) -> list[str]:
        due: list[str] = []
        for job_id in sorted(jobs):
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            candidate = job.get("next_tick_epoch_ms")
            if not isinstance(candidate, int):
                interval_seconds = _as_positive_float(job.get("interval_seconds"), default=0.001)
                candidate = now_ms + max(1, int(interval_seconds * 1000))
                job["next_tick_epoch_ms"] = candidate
            if now_ms >= candidate:
                due.append(job_id)
        return due

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


def _as_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, int) and value > 0:
        return int(value)
    if isinstance(value, float) and value > 0:
        return int(value)
    return int(default)


def _scheduler_lane_for_target(target: str) -> str:
    lowered = target.strip().lower()
    for lane in ("control", "data", "trace", "log", "metric"):
        if lowered.endswith(f":{lane}") or lowered.endswith(f"::{lane}"):
            return lane
    if lowered.startswith("system.obs.trace"):
        return "trace"
    if lowered.startswith("system.obs.log"):
        return "log"
    if lowered.startswith("system.obs.metric"):
        return "metric"
    return "control"


def _select_weighted_due(
    *,
    due_by_lane: dict[str, list[tuple[str, str, object, float]]],
    budget: int,
    lane_weights: dict[str, int],
) -> list[tuple[str, str, object, float]]:
    # Smooth weighted round-robin across lane queues for deterministic, starvation-safe draining.
    if budget <= 0:
        return []
    lanes = tuple(
        lane
        for lane in ("control", "data", "trace", "log", "metric")
        if due_by_lane.get(lane)
    )
    if not lanes:
        return []
    weights: dict[str, int] = {
        lane: _as_positive_int(lane_weights.get(lane), default=1)
        for lane in lanes
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return []
    current: dict[str, int] = {lane: 0 for lane in lanes}
    selected: list[tuple[str, str, object, float]] = []
    while len(selected) < budget:
        active_lanes = [lane for lane in lanes if due_by_lane.get(lane)]
        if not active_lanes:
            break
        best_lane: str | None = None
        best_score: int | None = None
        for lane in active_lanes:
            score = current.get(lane, 0) + weights[lane]
            current[lane] = score
            if best_lane is None or best_score is None or score > best_score:
                best_lane = lane
                best_score = score
        if best_lane is None:
            break
        current[best_lane] = current.get(best_lane, 0) - total_weight
        lane_queue = due_by_lane.get(best_lane)
        if not lane_queue:
            continue
        selected.append(lane_queue.pop(0))
    return selected


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
    "InMemoryPlatformSchedulerService",
]
