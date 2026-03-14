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


def test_platform_scheduler_dispatch_due_respects_tick_budget() -> None:
    service = InMemoryPlatformSchedulerService(
        store=InMemoryKvStore(),
        max_dispatch_per_tick=2,
    )
    commands = (
        PlatformSchedulerUpsertCommand(
            job_id="j.control",
            target="source:system.cp.command_ingress:control",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="j.data",
            target="source:system.cp.command_ingress:data",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="j.trace",
            target="source:system.cp.command_ingress:trace",
            interval_seconds=1.0,
            run_immediately=True,
        ),
    )
    for command in commands:
        service.apply_command(command, now_monotonic=1.0)

    first = service.dispatch_due(PlatformSchedulerTickEvent(), now_monotonic=1.0)
    second = service.dispatch_due(PlatformSchedulerTickEvent(), now_monotonic=1.0)

    assert len(first) == 2
    assert len(second) == 1
    assert {item.job_id for item in [*first, *second]} == {"j.control", "j.data", "j.trace"}


def test_platform_scheduler_dispatch_due_uses_weighted_lane_fairness() -> None:
    service = InMemoryPlatformSchedulerService(
        store=InMemoryKvStore(),
        max_dispatch_per_tick=6,
    )
    commands = (
        PlatformSchedulerUpsertCommand(
            job_id="data.1",
            target="source:system.cp.root_leaf_ingress:worker#1:data",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="data.2",
            target="source:system.cp.root_leaf_ingress:worker#2:data",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="data.3",
            target="source:system.cp.root_leaf_ingress:worker#3:data",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="control.1",
            target="source:system.cp.root_leaf_ingress:worker#1:control",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="control.2",
            target="source:system.cp.root_leaf_ingress:worker#2:control",
            interval_seconds=1.0,
            run_immediately=True,
        ),
        PlatformSchedulerUpsertCommand(
            job_id="trace.1",
            target="source:system.cp.root_leaf_ingress:worker#1:trace",
            interval_seconds=1.0,
            run_immediately=True,
        ),
    )
    for command in commands:
        service.apply_command(command, now_monotonic=2.0)

    due = service.dispatch_due(PlatformSchedulerTickEvent(), now_monotonic=2.0)

    assert [item.job_id for item in due] == [
        "data.1",
        "control.1",
        "data.2",
        "trace.1",
        "data.3",
        "control.2",
    ]
