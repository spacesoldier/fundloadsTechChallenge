from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneDiscoveryApplyNode,
    ControlPlaneDiscoveryFinalizeNode,
    ControlPlaneDiscoveryPumpNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _DiscoveryStreamService(ControlPlaneDiscoveryStreamService):
    start_events: list[object]
    request_events: list[object]

    def start(self, event: ControlPlaneDiscoveryStartRequestedEvent) -> list[object]:
        _ = event
        return list(self.start_events)

    def request_batch(self, event: ControlPlaneDiscoveryBatchRequestedEvent) -> list[object]:
        _ = event
        return list(self.request_events)


def test_discovery_pump_delegates_start_and_batch_requests_to_stream_service() -> None:
    runtime = {"platform": {"process_groups": []}}
    batch_request = ControlPlaneDiscoveryBatchRequestedEvent(
        session_id="s-1",
        source_scope="platform",
        cursor=0,
        limit=64,
    )
    completed = ControlPlaneDiscoveryCompletedEvent(runtime=runtime)
    service = _DiscoveryStreamService(start_events=[batch_request], request_events=[completed])
    pump = ControlPlaneDiscoveryPumpNode(stream=service)

    from_start = pump(ControlPlaneDiscoveryStartRequestedEvent(runtime=runtime), None)
    from_batch = pump(batch_request, None)

    assert len(from_start) == 1
    assert isinstance(from_start[0], Envelope)
    assert from_start[0].target == "system.cp.discovery_pump"
    assert from_start[0].payload == batch_request
    assert from_batch == [completed]


def test_discovery_apply_persists_batch_entities() -> None:
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    apply_node = ControlPlaneDiscoveryApplyNode(discovery=discovery)

    entity = ControlPlaneDiscoveryEntityRecord(
        entity_kind="node",
        entity_id="pkg.mod:NodeA",
        source_scope="platform",
        module="pkg.mod",
        qualname="NodeA",
        meta={"name": "node.a"},
    )
    batch = ControlPlaneDiscoveryBatchReadyEvent(
        session_id="s-1",
        source_scope="platform",
        cursor=0,
        entities=(entity,),
        has_more=False,
        next_cursor=None,
    )

    produced = apply_node(batch, None)

    assert produced == []
    assert discovery.items() == [entity]


def test_discovery_finalize_absorbs_source_completed_event() -> None:
    finalize = ControlPlaneDiscoveryFinalizeNode()

    produced = finalize(
        ControlPlaneDiscoverySourceCompletedEvent(
            session_id="s-1",
            source_scope="platform",
            total_emitted=1,
        ),
        None,
    )

    assert produced == []
