from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneDagAssemblyNode,
    ControlPlaneDiscoveryApplyNode,
    ControlPlaneDiscoveryFinalizeNode,
    ControlPlaneDiscoveryPumpNode,
    ControlPlaneInitPlanNode,
    ControlPlaneLeafApplyConfigNode,
    ControlPlaneLeafBootstrapNode,
    ControlPlaneRootBootstrapNode,
    ControlPlaneRootConfigStreamNode,
    ControlPlaneRootLeafConfigAckNode,
    ControlPlaneRootLeafConfigAssignNode,
    ControlPlaneStartupBarrierNode,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.system_nodes import (
    ControlPlaneSpawnDispatchNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    DefaultControlPlaneConfigStreamService,
    InMemoryControlPlaneStartupConfigStore,
    YamlControlPlaneConfigStreamAdapter,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneInitEvent,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafPulse,
    ControlPlaneRootPulse,
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    InMemoryControlPlaneStartupBarrierService,
)


@dataclass(slots=True)
class _Bootstrapper(ControlPlaneBootstrapperService):
    all_items: list[ControlPlaneDiscoveryItemEvent] = field(default_factory=list)

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = runtime
        return list(self.all_items)

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = (runtime, node_names)
        return []


@dataclass(slots=True)
class _LifecycleService:
    seen: list[ControlPlaneSpawnRequestedEvent] = field(default_factory=list)

    def on_spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> None:
        self.seen.append(event)


@dataclass(slots=True)
class _DiscoveryStream(ControlPlaneDiscoveryStreamService):
    runtime: dict[str, object]
    entities: tuple[ControlPlaneDiscoveryEntityRecord, ...]

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
        return [
            ControlPlaneDiscoveryBatchReadyEvent(
                session_id="s-1",
                source_scope="platform",
                cursor=0,
                entities=self.entities,
                has_more=False,
                next_cursor=None,
            ),
            ControlPlaneDiscoverySourceCompletedEvent(
                session_id="s-1",
                source_scope="platform",
                total_emitted=len(self.entities),
            ),
            ControlPlaneDiscoverySourceCompletedEvent(
                session_id="s-1",
                source_scope="project",
                total_emitted=0,
            ),
            ControlPlaneDiscoveryCompletedEvent(runtime=self.runtime),
        ]


def test_control_plane_handshake_sequence_reaches_root_state_update() -> None:
    root_runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": ["node.a", "node.b"]}],
        }
    }
    leaf_runtime = {
        "__process_group": "execution.alpha",
        "__worker_id": "execution.alpha#1",
        "__runner_profile_requested": "auto",
    }
    bootstrapper = _Bootstrapper(
        all_items=[
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"}),
            ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.b"}),
        ],
    )
    stream = _DiscoveryStream(
        runtime=root_runtime,
        entities=(
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="pkg.mod:NodeA",
                source_scope="platform",
                module="pkg.mod",
                qualname="NodeA",
                meta={"name": "node.a"},
            ),
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="pkg.mod:NodeB",
                source_scope="platform",
                module="pkg.mod",
                qualname="NodeB",
                meta={"name": "node.b"},
            ),
        ),
    )
    root_discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    leaf_discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    root_state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    lifecycle = _LifecycleService()

    root_bootstrap = ControlPlaneRootBootstrapNode()
    root_pump = ControlPlaneDiscoveryPumpNode(stream=stream)
    root_config_stream = ControlPlaneRootConfigStreamNode(
        config_stream=DefaultControlPlaneConfigStreamService(
            adapter=YamlControlPlaneConfigStreamAdapter(),
            store=InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore()),
        )
    )
    root_apply = ControlPlaneDiscoveryApplyNode(discovery=root_discovery)
    root_finalize = ControlPlaneDiscoveryFinalizeNode()
    root_barrier = ControlPlaneStartupBarrierNode(
        barrier=InMemoryControlPlaneStartupBarrierService()
    )
    root_dag_assembly = ControlPlaneDagAssemblyNode()
    root_init_plan = ControlPlaneInitPlanNode(state=root_state)
    root_spawn_dispatch = ControlPlaneSpawnDispatchNode(lifecycle=lifecycle)
    root_assign = ControlPlaneRootLeafConfigAssignNode(state=root_state)
    root_ack = ControlPlaneRootLeafConfigAckNode(state=root_state)
    leaf_bootstrap = ControlPlaneLeafBootstrapNode()
    leaf_apply = ControlPlaneLeafApplyConfigNode(bootstrapper=bootstrapper, discovery=leaf_discovery)

    # Root pulse -> discovery stream progression -> completed
    root_bootstrap_out = root_bootstrap(ControlPlaneRootPulse(runtime=root_runtime), None)
    root_discovery_events: list[object] = []
    for event in root_bootstrap_out:
        root_discovery_events.extend(root_pump(event, None))
    for event in list(root_discovery_events):
        root_discovery_events.extend(root_pump(event, None))
    assert any(isinstance(e, ControlPlaneDiscoveryCompletedEvent) for e in root_discovery_events)
    root_config_out = root_config_stream(ControlPlaneRootPulse(runtime=root_runtime), None)
    assert any(isinstance(e, ControlPlaneConfigStreamCompletedEvent) for e in root_config_out)

    # Discovery collect + startup barrier -> dag-assembly request only after both completed signals
    dag_requests: list[ControlPlaneDagAssemblyRequestedEvent] = []
    for event in root_discovery_events:
        root_apply(event, None)
        root_finalize(event, None)
        for emitted in root_barrier(event, None):
            if isinstance(emitted, ControlPlaneDagAssemblyRequestedEvent):
                dag_requests.append(emitted)
    for event in root_config_out:
        for emitted in root_barrier(event, None):
            if isinstance(emitted, ControlPlaneDagAssemblyRequestedEvent):
                dag_requests.append(emitted)
    assert len(dag_requests) == 1

    assembled_events: list[ControlPlaneDagAssembledEvent] = []
    for request in dag_requests:
        for emitted in root_dag_assembly(request, None):
            if isinstance(emitted, ControlPlaneDagAssembledEvent):
                assembled_events.append(emitted)
    assert len(assembled_events) == 1

    # Dag assembled -> init signal + launch plan + spawn requests
    plan_out = root_init_plan(assembled_events[0], None)
    assert any(isinstance(e, ControlPlaneInitEvent) for e in plan_out)
    assert any(isinstance(e, ControlPlaneLaunchPlanEvent) for e in plan_out)
    spawn_events = [e for e in plan_out if isinstance(e, ControlPlaneSpawnRequestedEvent)]
    assert len(spawn_events) == 1

    # Lifecycle bridge receives spawn request
    for event in spawn_events:
        assert root_spawn_dispatch(event, None) == []
    assert lifecycle.seen == spawn_events

    # Leaf pulse -> hello
    leaf_hello_out = leaf_bootstrap(ControlPlaneLeafPulse(runtime=leaf_runtime), None)
    assert len(leaf_hello_out) == 1
    assert isinstance(leaf_hello_out[0], ControlPlaneLeafHelloEvent)

    # Root assigns config card
    config_card_out = root_assign(leaf_hello_out[0], None)
    assert len(config_card_out) == 1
    assert isinstance(config_card_out[0], ControlPlaneLeafConfigCardEvent)

    # Leaf applies config and emits ack
    leaf_ack_out = leaf_apply(config_card_out[0], None)
    assert len(leaf_ack_out) == 1
    assert isinstance(leaf_ack_out[0], ControlPlaneLeafConfigAckEvent)
    assert leaf_ack_out[0].status == "applied"

    # Root consumes ack and updates state
    assert root_ack(leaf_ack_out[0], None) == []
    state_events = root_state.events()
    assert any(isinstance(e, ControlPlaneLaunchPlanEvent) for e in state_events)
    assert any(isinstance(e, ControlPlaneLeafHelloEvent) for e in state_events)
    assert any(isinstance(e, ControlPlaneLeafConfigCardEvent) for e in state_events)
    assert any(isinstance(e, ControlPlaneLeafConfigAckEvent) for e in state_events)
