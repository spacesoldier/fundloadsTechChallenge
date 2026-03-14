from __future__ import annotations

import asyncio

from stream_kernel.execution.orchestration.scheduler_system_nodes import (
    PlatformSchedulerCommandNode,
    PlatformSchedulerTimerNode,
    PlatformSchedulerTickNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.runtime.platform_scheduler import (
    AsyncioPlatformSchedulerTimerService,
    PlatformSchedulerCancelCommand,
    InMemoryPlatformSchedulerService,
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope


def test_scheduler_command_and_tick_nodes_route_due_payloads() -> None:
    scheduler = InMemoryPlatformSchedulerService(store=InMemoryKvStore())
    command_node = PlatformSchedulerCommandNode(scheduler=scheduler)
    tick_node = PlatformSchedulerTickNode(scheduler=scheduler)

    upsert = PlatformSchedulerUpsertCommand(
        job_id="cp.root.leaf_ingress.data",
        target="source:system.cp.root_leaf_ingress:execution.ingress#1:data",
        interval_seconds=60.0,
        run_immediately=True,
    )

    command_outputs = command_node(upsert, None)
    first_tick_outputs = tick_node(PlatformSchedulerTickEvent(), None)
    second_tick_outputs = tick_node(PlatformSchedulerTickEvent(), None)

    assert command_outputs == []
    assert len(first_tick_outputs) == 1
    assert isinstance(first_tick_outputs[0], Envelope)
    assert first_tick_outputs[0].target == upsert.target
    assert second_tick_outputs == []


def test_scheduler_timer_node_forwards_commands_to_timer_service() -> None:
    class _TimerStub:
        def __init__(self) -> None:
            self.commands: list[object] = []

        def apply_command(self, command: object) -> None:
            self.commands.append(command)

    timer = _TimerStub()
    node = PlatformSchedulerTimerNode(timer=timer)  # type: ignore[arg-type]
    upsert = PlatformSchedulerUpsertCommand(
        job_id="cp.root.leaf_ingress.data",
        target="source:system.cp.root_leaf_ingress:execution.ingress#1:data",
        interval_seconds=1.0,
        run_immediately=True,
    )

    outputs = node(upsert, None)

    assert outputs == []
    assert timer.commands == [upsert]


def test_asyncio_scheduler_timer_service_emits_tick_events_to_queue() -> None:
    async def _scenario() -> None:
        queue = InMemoryQueue()
        service = AsyncioPlatformSchedulerTimerService(
            store=InMemoryKvStore(),
            work_queue=queue,  # type: ignore[arg-type]
        )
        upsert = PlatformSchedulerUpsertCommand(
            job_id="cp.root.leaf_ingress.control",
            target="source:system.cp.root_leaf_ingress:execution.ingress#1:control",
            interval_seconds=0.01,
            run_immediately=True,
        )
        service.apply_command(upsert)
        await asyncio.sleep(0.02)
        queued = queue.pop()
        assert isinstance(queued, Envelope)
        assert isinstance(queued.payload, PlatformSchedulerTickEvent)
        assert queued.target == "system.scheduler.tick"
        snapshot = service.snapshot()
        assert any(item.job_id == upsert.job_id for item in snapshot.jobs)
        service.apply_command(PlatformSchedulerCancelCommand(job_id=upsert.job_id))
        service.close()

    asyncio.run(_scenario())


def test_asyncio_scheduler_timer_service_uses_single_background_task_for_multiple_jobs() -> None:
    async def _scenario() -> None:
        queue = InMemoryQueue()
        service = AsyncioPlatformSchedulerTimerService(
            store=InMemoryKvStore(),
            work_queue=queue,  # type: ignore[arg-type]
        )
        upsert_control = PlatformSchedulerUpsertCommand(
            job_id="cp.root.leaf_ingress.control",
            target="source:system.cp.root_leaf_ingress:execution.ingress#1:control",
            interval_seconds=0.05,
            run_immediately=False,
        )
        upsert_data = PlatformSchedulerUpsertCommand(
            job_id="cp.root.leaf_ingress.data",
            target="source:system.cp.root_leaf_ingress:execution.ingress#1:data",
            interval_seconds=0.05,
            run_immediately=False,
        )
        service.apply_command(upsert_control)
        service.apply_command(upsert_data)
        await asyncio.sleep(0.01)

        task = service._task  # type: ignore[attr-defined]
        assert task is not None
        assert not task.done()
        snapshot = service.snapshot()
        assert len(snapshot.jobs) == 2
        assert all(item.active for item in snapshot.jobs)

        service.apply_command(PlatformSchedulerCancelCommand(job_id=upsert_control.job_id))
        service.apply_command(PlatformSchedulerCancelCommand(job_id=upsert_data.job_id))
        await asyncio.sleep(0.01)
        service.close()
        await asyncio.sleep(0.01)
        task_after_close = service._task  # type: ignore[attr-defined]
        assert task_after_close is None or task_after_close.done()

    asyncio.run(_scenario())
