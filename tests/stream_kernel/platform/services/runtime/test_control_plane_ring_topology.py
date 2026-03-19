from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_ring_topology import (
    DefaultControlPlaneRingTopologyService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _ExecutionIpc:
    allocate_calls: list[tuple[str, bool]]

    def allocate_local_endpoints(
        self,
        target_id: str,
        *,
        register_parent_endpoint: bool = True,
    ) -> tuple[object, object]:
        self.allocate_calls.append((target_id, bool(register_parent_endpoint)))
        return (f"parent::{target_id}", f"child::{target_id}")


def test_ring_topology_service_ignores_non_ring_topology() -> None:
    ipc = _ExecutionIpc(allocate_calls=[])
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    store = InMemoryKvStore()
    service = DefaultControlPlaneRingTopologyService(execution_ipc=ipc, state=state, store=store)

    service.configure(
        runtime={"platform": {"execution_ipc": {"data_plane_topology": "star"}}},
        groups=[{"name": "execution.alpha", "workers": 1}],
    )

    assert service.plan() is None
    assert ipc.allocate_calls == []
    assert service.pop_child_endpoints(worker_id="execution.alpha#1") == {}


def test_ring_topology_service_builds_ring_links_and_child_endpoints() -> None:
    ipc = _ExecutionIpc(allocate_calls=[])
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    store = InMemoryKvStore()
    service = DefaultControlPlaneRingTopologyService(execution_ipc=ipc, state=state, store=store)

    service.configure(
        runtime={"platform": {"execution_ipc": {"data_plane_topology": "ring"}}},
        groups=[
            {"name": "execution.ingress", "workers": 1},
            {"name": "execution.features", "workers": 1},
            {"name": "execution.egress", "workers": 1},
            {"name": "system.observability", "workers": 1},
        ],
    )

    plan = service.plan()
    assert plan is not None
    assert plan.enabled is True
    assert plan.worker_ids == (
        "execution.ingress#1",
        "execution.features#1",
        "execution.egress#1",
    )
    assert len(plan.links) == 12
    assert ipc.allocate_calls == [
        ("ring:execution.ingress#1->execution.features#1:data", False),
        ("ring:execution.features#1->execution.egress#1:data", False),
        ("ring:execution.egress#1->execution.ingress#1:data", False),
        ("execution.ingress#1::trace", False),
        ("execution.ingress#1::log", False),
        ("execution.ingress#1::metric", False),
        ("execution.features#1::trace", False),
        ("execution.features#1::log", False),
        ("execution.features#1::metric", False),
        ("execution.egress#1::trace", False),
        ("execution.egress#1::log", False),
        ("execution.egress#1::metric", False),
    ]

    ingress_endpoints = service.pop_child_endpoints(worker_id="execution.ingress#1")
    features_endpoints = service.pop_child_endpoints(worker_id="execution.features#1")
    egress_endpoints = service.pop_child_endpoints(worker_id="execution.egress#1")
    observability_endpoints = service.pop_child_endpoints(worker_id="system.observability#1")
    assert ingress_endpoints == {
        "target::ring:execution.ingress#1->execution.features#1:data": (
            "parent::ring:execution.ingress#1->execution.features#1:data"
        ),
        "target::ring:execution.egress#1->execution.ingress#1:data": (
            "child::ring:execution.egress#1->execution.ingress#1:data"
        ),
        "target::execution.ingress#1::trace": "parent::execution.ingress#1::trace",
        "target::execution.ingress#1::log": "parent::execution.ingress#1::log",
        "target::execution.ingress#1::metric": "parent::execution.ingress#1::metric",
    }
    assert features_endpoints == {
        "target::ring:execution.ingress#1->execution.features#1:data": (
            "child::ring:execution.ingress#1->execution.features#1:data"
        ),
        "target::ring:execution.features#1->execution.egress#1:data": (
            "parent::ring:execution.features#1->execution.egress#1:data"
        ),
        "target::execution.features#1::trace": "parent::execution.features#1::trace",
        "target::execution.features#1::log": "parent::execution.features#1::log",
        "target::execution.features#1::metric": "parent::execution.features#1::metric",
    }
    assert egress_endpoints == {
        "target::ring:execution.features#1->execution.egress#1:data": (
            "child::ring:execution.features#1->execution.egress#1:data"
        ),
        "target::ring:execution.egress#1->execution.ingress#1:data": (
            "parent::ring:execution.egress#1->execution.ingress#1:data"
        ),
        "target::execution.egress#1::trace": "parent::execution.egress#1::trace",
        "target::execution.egress#1::log": "parent::execution.egress#1::log",
        "target::execution.egress#1::metric": "parent::execution.egress#1::metric",
    }
    assert observability_endpoints == {
        "target::execution.ingress#1::trace": "child::execution.ingress#1::trace",
        "target::execution.ingress#1::log": "child::execution.ingress#1::log",
        "target::execution.ingress#1::metric": "child::execution.ingress#1::metric",
        "target::execution.features#1::trace": "child::execution.features#1::trace",
        "target::execution.features#1::log": "child::execution.features#1::log",
        "target::execution.features#1::metric": "child::execution.features#1::metric",
        "target::execution.egress#1::trace": "child::execution.egress#1::trace",
        "target::execution.egress#1::log": "child::execution.egress#1::log",
        "target::execution.egress#1::metric": "child::execution.egress#1::metric",
    }
    assert service.pop_child_endpoints(worker_id="execution.ingress#1") == {}
    assert any(
        isinstance(event, dict) and event.get("kind") == "control_plane.ring_topology.configured"
        for event in state.events()
    )


def test_ring_topology_service_plan_is_readable_from_shared_store_instance() -> None:
    ipc = _ExecutionIpc(allocate_calls=[])
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    store = InMemoryKvStore()
    writer = DefaultControlPlaneRingTopologyService(execution_ipc=ipc, state=state, store=store)
    reader = DefaultControlPlaneRingTopologyService(execution_ipc=ipc, state=state, store=store)

    writer.configure(
        runtime={"platform": {"execution_ipc": {"data_plane_topology": "ring"}}},
        groups=[
            {"name": "execution.alpha", "workers": 1},
            {"name": "execution.beta", "workers": 1},
        ],
    )

    plan = reader.plan()
    assert plan is not None
    assert plan.enabled is True
    assert plan.worker_ids == ("execution.alpha#1", "execution.beta#1")
