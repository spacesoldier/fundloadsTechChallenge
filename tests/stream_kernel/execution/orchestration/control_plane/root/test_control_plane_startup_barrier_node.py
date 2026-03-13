from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneConfigApplyBarrierNode,
    ControlPlaneDiscoveryApplyNode,
    ControlPlaneDiscoveryFinalizeNode,
    ControlPlaneDiscoveryPumpNode,
    ControlPlaneNodeConfigApplyNode,
    ControlPlaneObservabilityConfigApplyNode,
    ControlPlaneRootConfigStreamNode,
    ControlPlaneRootBootstrapNode,
    ControlPlaneStartupBarrierNode,
    ControlPlaneSystemConfigApplyNode,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    DefaultControlPlaneConfigStreamService,
    InMemoryControlPlaneStartupConfigStore,
    YamlControlPlaneConfigStreamAdapter,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConfigApplyCompletedEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneRootPulse,
)
from stream_kernel.platform.services.runtime.control_plane_config_apply import (
    DefaultControlPlaneNodeConfigApplyService,
    DefaultControlPlaneObservabilityConfigApplyService,
    DefaultControlPlaneSystemConfigApplyService,
    InMemoryControlPlaneAppliedConfigStore,
    InMemoryControlPlaneConfigApplyTrackerService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    InMemoryControlPlaneStartupBarrierService,
)


class _DiscoveryStreamService(ControlPlaneDiscoveryStreamService):
    def __init__(self, runtime: dict[str, object]) -> None:
        self.runtime = runtime

    def start(self, event: ControlPlaneDiscoveryStartRequestedEvent) -> list[object]:
        _ = event
        return [
            ControlPlaneDiscoveryBatchRequestedEvent(
                session_id="s-1",
                source_scope="platform",
                cursor=0,
                limit=128,
            )
        ]

    def request_batch(self, event: ControlPlaneDiscoveryBatchRequestedEvent) -> list[object]:
        _ = event
        entity = ControlPlaneDiscoveryEntityRecord(
            entity_kind="node",
            entity_id="pkg.mod:NodeA",
            source_scope="platform",
            module="pkg.mod",
            qualname="NodeA",
            meta={"name": "node.a"},
        )
        return [
            ControlPlaneDiscoveryBatchReadyEvent(
                session_id="s-1",
                source_scope="platform",
                cursor=0,
                entities=(entity,),
                has_more=False,
                next_cursor=None,
            ),
            ControlPlaneDiscoverySourceCompletedEvent(
                session_id="s-1",
                source_scope="platform",
                total_emitted=1,
            ),
            ControlPlaneDiscoverySourceCompletedEvent(
                session_id="s-1",
                source_scope="project",
                total_emitted=0,
            ),
            ControlPlaneDiscoveryCompletedEvent(runtime=self.runtime),
        ]


def test_startup_barrier_node_emits_dag_assembly_request_only_after_both_completed_events() -> None:
    barrier = InMemoryControlPlaneStartupBarrierService()
    node = ControlPlaneStartupBarrierNode(barrier=barrier)
    runtime = {"platform": {"process_groups": []}}

    out_after_discovery = node(ControlPlaneDiscoveryCompletedEvent(runtime=runtime), None)
    out_after_config = node(
        ControlPlaneConfigApplyCompletedEvent(runtime=runtime, expected_counts={}, applied_counts={}),
        None,
    )

    assert out_after_discovery == []
    assert out_after_config == [ControlPlaneDagAssemblyRequestedEvent(runtime=runtime)]

    # Duplicates must not emit new init events.
    assert (
        node(
            ControlPlaneConfigApplyCompletedEvent(runtime=runtime, expected_counts={}, applied_counts={}),
            None,
        )
        == []
    )
    assert node(ControlPlaneDiscoveryCompletedEvent(runtime=runtime), None) == []


def test_root_pulse_chain_waits_for_startup_barrier_before_spawn_events() -> None:
    runtime = {
        "strict": True,
        "platform": {
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": ["node.a"]}]
        },
    }
    root_bootstrap = ControlPlaneRootBootstrapNode()
    root_pump = ControlPlaneDiscoveryPumpNode(stream=_DiscoveryStreamService(runtime=runtime))
    root_apply = ControlPlaneDiscoveryApplyNode(
        discovery=InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    )
    root_finalize = ControlPlaneDiscoveryFinalizeNode()
    config_store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    root_config_stream = ControlPlaneRootConfigStreamNode(
        config_stream=DefaultControlPlaneConfigStreamService(
            adapter=YamlControlPlaneConfigStreamAdapter(),
            store=config_store,
        )
    )
    config_apply_barrier = ControlPlaneConfigApplyBarrierNode(
        tracker=InMemoryControlPlaneConfigApplyTrackerService(),
        config_store=config_store,
    )
    applied_config_store = InMemoryControlPlaneAppliedConfigStore(store=InMemoryKvStore())
    system_config_apply = ControlPlaneSystemConfigApplyNode(
        applier=DefaultControlPlaneSystemConfigApplyService(store=applied_config_store)
    )
    observability_config_apply = ControlPlaneObservabilityConfigApplyNode(
        applier=DefaultControlPlaneObservabilityConfigApplyService(store=applied_config_store)
    )
    node_config_apply = ControlPlaneNodeConfigApplyNode(
        applier=DefaultControlPlaneNodeConfigApplyService(store=applied_config_store)
    )
    startup_barrier = ControlPlaneStartupBarrierNode(
        barrier=InMemoryControlPlaneStartupBarrierService()
    )

    bootstrap_events = root_bootstrap(ControlPlaneRootPulse(runtime=runtime), None)
    root_events: list[object] = []
    for event in bootstrap_events:
        root_events.extend(root_pump(event, None))
    for event in list(root_events):
        root_events.extend(root_pump(event, None))

    config_events = root_config_stream(ControlPlaneRootPulse(runtime=runtime), None)

    dag_requests_before_open: list[object] = []
    for event in root_events:
        dag_requests_before_open.extend(root_apply(event, None))
        dag_requests_before_open.extend(root_finalize(event, None))
        dag_requests_before_open.extend(startup_barrier(event, None))
    assert dag_requests_before_open == []

    config_apply_events: list[object] = []
    for event in config_events:
        config_apply_events.extend(system_config_apply(event, None))
        config_apply_events.extend(observability_config_apply(event, None))
        config_apply_events.extend(node_config_apply(event, None))
        config_apply_events.extend(config_apply_barrier(event, None))
    for event in list(config_apply_events):
        config_apply_events.extend(config_apply_barrier(event, None))

    dag_requests_after_open: list[object] = []
    for event in config_apply_events:
        dag_requests_after_open.extend(startup_barrier(event, None))
    assert len(dag_requests_after_open) == 1
    assert isinstance(dag_requests_after_open[0], ControlPlaneDagAssemblyRequestedEvent)
