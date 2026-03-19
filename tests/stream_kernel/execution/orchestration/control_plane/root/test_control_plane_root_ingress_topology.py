from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.root.plan_builder import (
    _build_root_leaf_ingress_source_steps,
    _root_leaf_ingress_lanes_for_worker,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_nodes import (
    ROOT_LEAF_INGRESS_SOURCE_NODE_NAME,
    ControlPlaneRootLeafIngressSourceNode,
)


@dataclass(slots=True)
class _IngressStub:
    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ) -> object | None:
        _ = (worker_id, lane, timeout_seconds)
        return None


def _runtime(*, topology: str) -> dict[str, object]:
    return {
        "platform": {
            "execution_ipc": {"data_plane_topology": topology},
            "process_groups": [
                {"name": "execution.ingress", "workers": 1},
                {"name": "execution.features", "workers": 1},
                {"name": "execution.egress", "workers": 1},
                {"name": "system.observability", "workers": 1},
            ],
        },
        "observability": {
            "service_process": {
                "enabled": True,
                "group_name": "system.observability",
                "workers": 1,
            }
        },
    }


def test_root_leaf_ingress_lanes_for_worker_star_topology_keeps_data_for_all() -> None:
    runtime = _runtime(topology="star")

    ingress_lanes = _root_leaf_ingress_lanes_for_worker(
        runtime=runtime,
        worker_id="execution.ingress#1",
    )
    observability_lanes = _root_leaf_ingress_lanes_for_worker(
        runtime=runtime,
        worker_id="system.observability#1",
    )

    assert ingress_lanes == ("control", "data")
    assert observability_lanes == ("control", "data")


def test_root_leaf_ingress_lanes_for_worker_ring_topology_keeps_data_only_for_observability_worker() -> None:
    runtime = _runtime(topology="ring")

    ingress_lanes = _root_leaf_ingress_lanes_for_worker(
        runtime=runtime,
        worker_id="execution.ingress#1",
    )
    observability_lanes = _root_leaf_ingress_lanes_for_worker(
        runtime=runtime,
        worker_id="system.observability#1",
    )

    assert ingress_lanes == ("control",)
    assert observability_lanes == ("control", "data")


def test_build_root_leaf_ingress_sources_ring_topology_builds_single_drain_with_expected_specs() -> None:
    runtime = _runtime(topology="ring")

    steps = _build_root_leaf_ingress_source_steps(
        runtime=runtime,
        scope=object(),
        resolve_required_service=lambda **_: _IngressStub(),
        root_leaf_ingress_contract=lambda: object,
    )
    assert len(steps) == 1
    assert steps[0].name == ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
    node = steps[0].step
    assert isinstance(node, ControlPlaneRootLeafIngressSourceNode)
    assert ("execution.ingress#1", "control") in node.ingress_specs
    assert ("execution.features#1", "control") in node.ingress_specs
    assert ("execution.egress#1", "control") in node.ingress_specs
    assert ("system.observability#1", "control") in node.ingress_specs
    assert ("system.observability#1", "data") in node.ingress_specs
