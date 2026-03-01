from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoverySourceAdapter,
    DefaultControlPlaneDiscoveryStreamService,
    InMemoryControlPlaneDiscoverySessionStore,
)


@dataclass(slots=True)
class _Adapter(ControlPlaneDiscoverySourceAdapter):
    batches: dict[int, tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]]
    calls: list[tuple[int, int]]

    def next_batch(
        self,
        *,
        runtime: dict[str, object],
        cursor: int,
        limit: int,
    ) -> tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]:
        _ = runtime
        self.calls.append((cursor, limit))
        return self.batches.get(cursor, (tuple(), None))


def _record(scope: str, module: str, qualname: str, *, kind: str = "node") -> ControlPlaneDiscoveryEntityRecord:
    return ControlPlaneDiscoveryEntityRecord(
        entity_kind=kind,
        entity_id=f"{module}:{qualname}",
        source_scope=scope,
        module=module,
        qualname=qualname,
        meta={},
    )


def _new_service(
    *,
    platform_batches: dict[int, tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]],
    project_batches: dict[int, tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]],
) -> tuple[DefaultControlPlaneDiscoveryStreamService, _Adapter, _Adapter]:
    platform = _Adapter(batches=platform_batches, calls=[])
    project = _Adapter(batches=project_batches, calls=[])
    service = DefaultControlPlaneDiscoveryStreamService(
        platform_adapter=platform,
        project_adapter=project,
        session_store=InMemoryControlPlaneDiscoverySessionStore(store=InMemoryKvStore()),
    )
    return service, platform, project


def test_discovery_stream_service_opens_session_and_emits_first_platform_batch_request() -> None:
    service, _, _ = _new_service(platform_batches={}, project_batches={})

    produced = service.start(ControlPlaneDiscoveryStartRequestedEvent(runtime={"platform": {}}))

    assert len(produced) == 1
    first = produced[0]
    assert isinstance(first, ControlPlaneDiscoveryBatchRequestedEvent)
    assert first.source_scope == "platform"
    assert first.cursor == 0
    assert first.limit == 128


def test_discovery_stream_service_merges_platform_then_project_deterministically() -> None:
    service, platform, project = _new_service(
        platform_batches={
            0: (
                (
                    _record("platform", "z.mod", "NodeZ"),
                    _record("platform", "a.mod", "NodeA"),
                ),
                None,
            )
        },
        project_batches={
            0: (
                (
                    _record("project", "b.mod", "NodeB"),
                    _record("project", "a.mod", "NodeA"),
                ),
                None,
            )
        },
    )

    start_events = service.start(ControlPlaneDiscoveryStartRequestedEvent(runtime={"platform": {}}))
    first_request = start_events[0]
    assert isinstance(first_request, ControlPlaneDiscoveryBatchRequestedEvent)

    # Project request before platform completion should be ignored to preserve deterministic merge order.
    blocked = service.request_batch(
        ControlPlaneDiscoveryBatchRequestedEvent(
            session_id=first_request.session_id,
            source_scope="project",
            cursor=0,
            limit=first_request.limit,
        )
    )
    assert blocked == []
    assert project.calls == []

    platform_events = service.request_batch(first_request)
    assert isinstance(platform_events[0], ControlPlaneDiscoveryBatchReadyEvent)
    platform_ready = platform_events[0]
    assert [item.entity_id for item in platform_ready.entities] == [
        "a.mod:NodeA",
        "z.mod:NodeZ",
    ]
    assert isinstance(platform_events[1], ControlPlaneDiscoverySourceCompletedEvent)
    assert isinstance(platform_events[2], ControlPlaneDiscoveryBatchRequestedEvent)
    assert platform_events[2].source_scope == "project"

    project_events = service.request_batch(platform_events[2])
    assert isinstance(project_events[0], ControlPlaneDiscoveryBatchReadyEvent)
    project_ready = project_events[0]
    assert [item.entity_id for item in project_ready.entities] == [
        "a.mod:NodeA",
        "b.mod:NodeB",
    ]
    assert isinstance(project_events[1], ControlPlaneDiscoverySourceCompletedEvent)
    assert isinstance(project_events[2], ControlPlaneDiscoveryCompletedEvent)

    assert platform.calls == [(0, 128)]
    assert project.calls == [(0, 128)]


def test_discovery_stream_service_emits_source_completed_and_final_completed_event() -> None:
    service, _, _ = _new_service(
        platform_batches={0: ((_record("platform", "a.mod", "NodeA"),), None)},
        project_batches={0: ((_record("project", "b.mod", "NodeB"),), None)},
    )

    start_events = service.start(ControlPlaneDiscoveryStartRequestedEvent(runtime={"strict": True}))
    first_request = start_events[0]
    assert isinstance(first_request, ControlPlaneDiscoveryBatchRequestedEvent)

    platform_events = service.request_batch(first_request)
    project_request = platform_events[2]
    assert isinstance(project_request, ControlPlaneDiscoveryBatchRequestedEvent)

    project_events = service.request_batch(project_request)

    source_completed = [
        event
        for event in [*platform_events, *project_events]
        if isinstance(event, ControlPlaneDiscoverySourceCompletedEvent)
    ]
    assert [(event.source_scope, event.total_emitted) for event in source_completed] == [
        ("platform", 1),
        ("project", 1),
    ]

    completed = [event for event in project_events if isinstance(event, ControlPlaneDiscoveryCompletedEvent)]
    assert len(completed) == 1
    assert completed[0].runtime == {"strict": True}


def test_discovery_stream_service_is_idempotent_for_duplicate_requests() -> None:
    service, _, _ = _new_service(
        platform_batches={0: ((_record("platform", "a.mod", "NodeA"),), None)},
        project_batches={0: ((_record("project", "b.mod", "NodeB"),), None)},
    )

    start_events = service.start(ControlPlaneDiscoveryStartRequestedEvent(runtime={}))
    first_request = start_events[0]
    assert isinstance(first_request, ControlPlaneDiscoveryBatchRequestedEvent)

    first_platform = service.request_batch(first_request)
    duplicate_platform = service.request_batch(first_request)
    assert duplicate_platform == []

    project_request = first_platform[2]
    assert isinstance(project_request, ControlPlaneDiscoveryBatchRequestedEvent)
    first_project = service.request_batch(project_request)
    duplicate_project = service.request_batch(project_request)
    assert duplicate_project == []

    completed_events = [
        event
        for event in [*first_platform, *first_project, *duplicate_platform, *duplicate_project]
        if isinstance(event, ControlPlaneDiscoverySourceCompletedEvent)
        or isinstance(event, ControlPlaneDiscoveryCompletedEvent)
    ]
    assert [type(event).__name__ for event in completed_events] == [
        "ControlPlaneDiscoverySourceCompletedEvent",
        "ControlPlaneDiscoverySourceCompletedEvent",
        "ControlPlaneDiscoveryCompletedEvent",
    ]
