from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.platform_scheduler import (
    InMemoryPlatformSchedulerService,
    PlatformSchedulerCancelCommand,
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)


def test_platform_scheduler_dispatches_due_jobs_and_reschedules() -> None:
    service = InMemoryPlatformSchedulerService(store=InMemoryKvStore())
    command = PlatformSchedulerUpsertCommand(
        job_id="cp.root.leaf_ingress.control",
        target="source:system.cp.root_leaf_ingress:execution.ingress#1:control",
        interval_seconds=0.5,
        run_immediately=True,
    )

    service.apply_command(command, now_monotonic=10.0)

    first = service.dispatch_due(
        PlatformSchedulerTickEvent(),
        now_monotonic=10.0,
    )
    second = service.dispatch_due(
        PlatformSchedulerTickEvent(),
        now_monotonic=10.1,
    )
    third = service.dispatch_due(
        PlatformSchedulerTickEvent(),
        now_monotonic=10.5,
    )

    assert len(first) == 1
    assert first[0].job_id == command.job_id
    assert first[0].target == command.target
    assert first[0].payload is None
    assert second == []
    assert len(third) == 1
    assert third[0].job_id == command.job_id


def test_platform_scheduler_cancel_removes_job() -> None:
    service = InMemoryPlatformSchedulerService(store=InMemoryKvStore())
    service.apply_command(
        PlatformSchedulerUpsertCommand(
            job_id="cp.leaf.command_ingress.control",
            target="source:system.cp.command_ingress:control",
            interval_seconds=1.0,
        ),
        now_monotonic=42.0,
    )
    service.apply_command(
        PlatformSchedulerCancelCommand(job_id="cp.leaf.command_ingress.control"),
        now_monotonic=42.1,
    )

    due = service.dispatch_due(
        PlatformSchedulerTickEvent(),
        now_monotonic=43.0,
    )
    snapshot = service.snapshot()

    assert due == []
    assert snapshot.jobs == ()


def test_platform_scheduler_preserves_explicit_payload() -> None:
    service = InMemoryPlatformSchedulerService(store=InMemoryKvStore())
    payload = {"kind": "custom.tick"}
    command = PlatformSchedulerUpsertCommand(
        job_id="custom",
        target="system.scheduler.target",
        interval_seconds=0.25,
        payload=payload,
    )

    service.apply_command(command, now_monotonic=5.0)
    due = service.dispatch_due(PlatformSchedulerTickEvent(), now_monotonic=5.0)

    assert len(due) == 1
    assert due[0].target == "system.scheduler.target"
    assert due[0].payload == payload
