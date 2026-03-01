from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneConfigStreamService,
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_config_apply import (
    ControlPlaneConfigApplyTrackerService,
    ControlPlaneNodeConfigApplyService,
    ControlPlaneObservabilityConfigApplyService,
    ControlPlaneSystemConfigApplyService,
    resolve_expected_config_apply_counts,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    ControlPlaneDagAssemblyService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneConfigApplyCompletedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    ExecutionGroupConfigRecord,
    ControlPlaneGroupSpec,
    ControlPlaneInitEvent,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
    ControlPlaneRootLeafStopRequestEvent,
    ControlPlaneRootPulse,
    ControlPlaneSpawnRequestedEvent,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
    ControlPlaneNodeConfigAppliedEvent,
    ControlPlaneObservabilityConfigAppliedEvent,
    ControlPlaneSystemConfigAppliedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.routing.envelope import Envelope


@node(
    name="system.cp.dag_assembly",
    consumes=[ControlPlaneDagAssemblyRequestedEvent],
    emits=[ControlPlaneDagAssembledEvent],
)
@dataclass
class ControlPlaneDagAssemblyNode:
    assembly: ControlPlaneDagAssemblyService = inject.service(ControlPlaneDagAssemblyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDagAssemblyRequestedEvent):
            return []
        plan = self.assembly.assemble(runtime=payload.runtime)
        if not isinstance(plan, ControlPlaneLaunchPlan) or not plan.groups:
            return []
        return [ControlPlaneDagAssembledEvent(runtime=payload.runtime, plan=plan)]


@node(
    name="system.cp.init_plan",
    consumes=[ControlPlaneDagAssembledEvent],
    emits=[ControlPlaneInitEvent, ControlPlaneLaunchPlanEvent, ControlPlaneSpawnRequestedEvent],
)
@dataclass
class ControlPlaneInitPlanNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDagAssembledEvent):
            return []
        plan_signature = _launch_plan_signature(payload.plan)
        for event in reversed(self.state.events()):
            if not isinstance(event, ControlPlaneLaunchPlanEvent):
                continue
            if _launch_plan_signature(event.plan) == plan_signature:
                # Idempotency guard: repeated startup pulses must not re-emit spawn requests.
                return []
        plan_event = ControlPlaneLaunchPlanEvent(plan=payload.plan)
        self.state.append_event(plan_event)
        spawn_events = [
            ControlPlaneSpawnRequestedEvent(
                group_name=group.group_name,
                workers=group.workers,
                nodes=tuple(group.nodes),
            )
            for group in payload.plan.groups
        ]
        return [plan_event, ControlPlaneInitEvent(runtime=payload.runtime), *spawn_events]


@node(
    name="system.cp.root_bootstrap",
    consumes=[ControlPlaneRootPulse],
    emits=[ControlPlaneDiscoveryStartRequestedEvent],
)
@dataclass
class ControlPlaneRootBootstrapNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootPulse):
            return []
        return [ControlPlaneDiscoveryStartRequestedEvent(runtime=payload.runtime)]


@node(
    name="system.cp.root_config_stream",
    consumes=[ControlPlaneRootPulse],
    emits=[
        SystemRuntimeConfigRecord,
        ObservabilityConfigRecord,
        ExecutionGroupConfigRecord,
        NodeConfigRecord,
        ControlPlaneConfigStreamCompletedEvent,
    ],
)
@dataclass
class ControlPlaneRootConfigStreamNode:
    config_stream: ControlPlaneConfigStreamService = inject.service(ControlPlaneConfigStreamService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootPulse):
            return []
        return list(self.config_stream.stream(payload.runtime))


@node(
    name="system.cp.system_config_apply",
    consumes=[SystemRuntimeConfigRecord],
    emits=[ControlPlaneSystemConfigAppliedEvent],
)
@dataclass
class ControlPlaneSystemConfigApplyNode:
    applier: ControlPlaneSystemConfigApplyService = inject.service(ControlPlaneSystemConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, SystemRuntimeConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        return [ControlPlaneSystemConfigAppliedEvent(record_id=payload.record_id)]


@node(
    name="system.cp.observability_config_apply",
    consumes=[ObservabilityConfigRecord],
    emits=[ControlPlaneObservabilityConfigAppliedEvent],
)
@dataclass
class ControlPlaneObservabilityConfigApplyNode:
    applier: ControlPlaneObservabilityConfigApplyService = inject.service(ControlPlaneObservabilityConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ObservabilityConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        return [ControlPlaneObservabilityConfigAppliedEvent(record_id=payload.record_id)]


@node(
    name="system.cp.node_config_apply",
    consumes=[NodeConfigRecord],
    emits=[ControlPlaneNodeConfigAppliedEvent],
)
@dataclass
class ControlPlaneNodeConfigApplyNode:
    applier: ControlPlaneNodeConfigApplyService = inject.service(ControlPlaneNodeConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, NodeConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        node_name = payload.payload.get("name")
        return [
            ControlPlaneNodeConfigAppliedEvent(
                record_id=payload.record_id,
                node_name=node_name if isinstance(node_name, str) and node_name else None,
            )
        ]


@node(
    name="system.cp.config_apply_barrier",
    consumes=[
        ControlPlaneConfigStreamCompletedEvent,
        ControlPlaneSystemConfigAppliedEvent,
        ControlPlaneObservabilityConfigAppliedEvent,
        ControlPlaneNodeConfigAppliedEvent,
    ],
    emits=[ControlPlaneConfigApplyCompletedEvent],
)
@dataclass
class ControlPlaneConfigApplyBarrierNode:
    tracker: ControlPlaneConfigApplyTrackerService = inject.service(ControlPlaneConfigApplyTrackerService)
    config_store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        completed_now = False
        if isinstance(payload, ControlPlaneConfigStreamCompletedEvent):
            expected = resolve_expected_config_apply_counts(self.config_store)
            completed_now = self.tracker.mark_stream_completed(
                runtime=payload.runtime,
                expected_counts=expected,
            )
        elif isinstance(payload, ControlPlaneSystemConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="system_runtime")
        elif isinstance(payload, ControlPlaneObservabilityConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="observability")
        elif isinstance(payload, ControlPlaneNodeConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="node")
        if not completed_now:
            return []
        progress = self.tracker.progress()
        runtime = progress.runtime if isinstance(progress.runtime, dict) else {}
        return [
            ControlPlaneConfigApplyCompletedEvent(
                runtime=runtime,
                expected_counts=dict(progress.expected_counts),
                applied_counts=dict(progress.applied_counts),
            )
        ]


@node(
    name="system.cp.discovery_pump",
    consumes=[ControlPlaneDiscoveryStartRequestedEvent, ControlPlaneDiscoveryBatchRequestedEvent],
    emits=[
        ControlPlaneDiscoveryBatchRequestedEvent,
        ControlPlaneDiscoveryBatchReadyEvent,
        ControlPlaneDiscoverySourceCompletedEvent,
        ControlPlaneDiscoveryCompletedEvent,
    ],
)
@dataclass
class ControlPlaneDiscoveryPumpNode:
    stream: ControlPlaneDiscoveryStreamService = inject.service(ControlPlaneDiscoveryStreamService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoveryStartRequestedEvent):
            return _target_discovery_pump_requests(list(self.stream.start(payload)))
        if isinstance(payload, ControlPlaneDiscoveryBatchRequestedEvent):
            return _target_discovery_pump_requests(list(self.stream.request_batch(payload)))
        return []


def _target_discovery_pump_requests(events: list[object]) -> list[object]:
    # Discovery pump can schedule next batch requests back to itself.
    # Emit those requests with explicit target to avoid strict-router implicit self-loop rejection.
    routed: list[object] = []
    for event in events:
        if isinstance(event, ControlPlaneDiscoveryBatchRequestedEvent):
            routed.append(
                Envelope(
                    payload=event,
                    target="system.cp.discovery_pump",
                )
            )
            continue
        routed.append(event)
    return routed


@node(
    name="system.cp.discovery_apply",
    consumes=[ControlPlaneDiscoveryBatchReadyEvent],
    emits=[],
)
@dataclass
class ControlPlaneDiscoveryApplyNode:
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoveryBatchReadyEvent):
            for entity in payload.entities:
                if isinstance(entity, ControlPlaneDiscoveryEntityRecord):
                    self.discovery.append_item(entity)
            return []
        return []


@node(
    name="system.cp.discovery_finalize",
    consumes=[ControlPlaneDiscoverySourceCompletedEvent],
    emits=[],
)
@dataclass
class ControlPlaneDiscoveryFinalizeNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoverySourceCompletedEvent):
            return []
        return []


@node(
    name="system.cp.startup_barrier",
    consumes=[ControlPlaneDiscoveryCompletedEvent, ControlPlaneConfigApplyCompletedEvent],
    emits=[ControlPlaneDagAssemblyRequestedEvent],
)
@dataclass
class ControlPlaneStartupBarrierNode:
    barrier: ControlPlaneStartupBarrierService = inject.service(ControlPlaneStartupBarrierService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        opened_now = False
        runtime: dict[str, object] | None = None
        if isinstance(payload, ControlPlaneDiscoveryCompletedEvent):
            runtime = payload.runtime
            opened_now = self.barrier.mark_discovery_completed(runtime=runtime)
        elif isinstance(payload, ControlPlaneConfigApplyCompletedEvent):
            runtime = payload.runtime
            opened_now = self.barrier.mark_config_completed(runtime=runtime)
        if opened_now and isinstance(runtime, dict):
            return [ControlPlaneDagAssemblyRequestedEvent(runtime=runtime)]
        return []


@dataclass
class ControlPlaneRootLeafConfigAssignNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafHelloEvent):
            return []
        self.state.append_event(payload)
        events = self.state.events()
        group = _find_group_spec_from_state(events, payload.target_group)
        if group is None:
            return []
        revision = _next_config_revision(events, payload.worker_id)
        card = ControlPlaneLeafConfigCardEvent(
            target_group=group.group_name,
            worker_id=payload.worker_id,
            config_id=f"{payload.worker_id}:cfg:{revision}",
            run_id="run",
            scenario_id="scenario",
            group_name=group.group_name,
            nodes=tuple(group.nodes),
            runner_profile=payload.runner_profile,
            worker_slot=_worker_slot_from_worker_id(payload.worker_id),
            config_revision=revision,
        )
        self.state.append_event(card)
        return [card]


@dataclass
class ControlPlaneRootLeafConfigAckNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigAckEvent):
            return []
        self.state.append_event(payload)
        return []


@dataclass
class ControlPlaneRootLeafStopDispatchNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafStopRequestEvent):
            return []
        self.state.append_event(payload)
        command = ControlPlaneLeafStopCommand(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            command_id=payload.command_id,
            reason=payload.reason,
        )
        self.state.append_event(command)
        return [command]


@dataclass
class ControlPlaneRootLeafStopAckNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStopAckEvent):
            return []
        self.state.append_event(payload)
        return []


@dataclass
class ControlPlaneRootLeafBoundaryDispatchNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafBoundaryExecuteRequestEvent):
            return []
        self.state.append_event(payload)
        command = ControlPlaneLeafBoundaryExecuteCommand(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            request_id=payload.request_id,
            inputs=tuple(payload.inputs),
            finalize=payload.finalize,
        )
        self.state.append_event(command)
        return [command]


@dataclass
class ControlPlaneRootLeafBoundaryResultNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
            return []
        self.state.append_event(payload)
        return []


def resolve_group_specs(
    runtime: dict[str, object],
    *,
    config_store: ControlPlaneStartupConfigStore | object | None = None,
    discovered_node_names: set[str] | None = None,
) -> list[ControlPlaneGroupSpec]:
    from_store = _resolve_group_specs_from_store(
        config_store,
        discovered_node_names=discovered_node_names,
    )
    if from_store:
        return from_store
    if not isinstance(runtime, dict):
        return []
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if not isinstance(name, str) or not name:
            continue
        workers = group.get("workers", 1)
        if not isinstance(workers, int) or workers <= 0:
            continue
        raw_nodes = group.get("nodes", [])
        nodes = (
            tuple(node_name for node_name in raw_nodes if isinstance(node_name, str) and node_name)
            if isinstance(raw_nodes, list)
            else ()
        )
        nodes = _filter_group_nodes(nodes, discovered_node_names=discovered_node_names)
        resolved.append(ControlPlaneGroupSpec(group_name=name, workers=workers, nodes=nodes))
    return resolved


def _resolve_group_specs_from_store(
    config_store: ControlPlaneStartupConfigStore | object | None,
    *,
    discovered_node_names: set[str] | None = None,
) -> list[ControlPlaneGroupSpec]:
    store = _config_store_optional(config_store)
    if store is None:
        return []
    try:
        records = list(store.records(section="execution_group"))
    except Exception:
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for record in records:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            continue
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            continue
        workers = payload.get("workers", 1)
        if not isinstance(workers, int) or workers <= 0:
            continue
        raw_nodes = payload.get("nodes", [])
        nodes = (
            tuple(node_name for node_name in raw_nodes if isinstance(node_name, str) and node_name)
            if isinstance(raw_nodes, list)
            else ()
        )
        nodes = _filter_group_nodes(nodes, discovered_node_names=discovered_node_names)
        resolved.append(ControlPlaneGroupSpec(group_name=name, workers=workers, nodes=nodes))
    return resolved


def _config_store_optional(
    value: ControlPlaneStartupConfigStore | object | None,
) -> ControlPlaneStartupConfigStore | None:
    if isinstance(value, ControlPlaneStartupConfigStore):
        return value
    if callable(getattr(value, "records", None)) and callable(getattr(value, "all_records", None)):
        return value  # type: ignore[return-value]
    return None


def _discovered_node_names(value: ControlPlaneDiscoveryService | object | None) -> set[str] | None:
    if isinstance(value, ControlPlaneDiscoveryService):
        service = value
    elif callable(getattr(value, "items", None)):
        service = value  # type: ignore[assignment]
    else:
        return None
    items: list[object]
    try:
        records_method = getattr(service, "entity_records", None)
        if callable(records_method):
            items = list(records_method(kind="node"))
        else:
            items = list(service.items())  # type: ignore[call-arg]
    except Exception:
        return None
    names: set[str] = set()
    for item in items:
        if isinstance(item, ControlPlaneDiscoveryEntityRecord):
            name = item.meta.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    if not names:
        return None
    return names


def _filter_group_nodes(
    nodes: tuple[str, ...],
    *,
    discovered_node_names: set[str] | None,
) -> tuple[str, ...]:
    if not discovered_node_names:
        return nodes
    return tuple(name for name in nodes if name in discovered_node_names)


def find_group_spec_from_state(events: list[object], target_group: str) -> ControlPlaneGroupSpec | None:
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLaunchPlanEvent):
            continue
        for group in event.plan.groups:
            if group.group_name == target_group:
                return group
    return None


def next_config_revision(events: list[object], worker_id: str) -> int:
    count = 0
    for event in events:
        if isinstance(event, ControlPlaneLeafConfigCardEvent) and event.worker_id == worker_id:
            count += 1
    return count + 1


def worker_slot_from_worker_id(worker_id: str) -> int | None:
    if "#" not in worker_id:
        return None
    _, raw_index = worker_id.rsplit("#", 1)
    try:
        parsed = int(raw_index)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed - 1


# private aliases kept to minimize call-site churn in this module
_find_group_spec_from_state = find_group_spec_from_state
_next_config_revision = next_config_revision
_worker_slot_from_worker_id = worker_slot_from_worker_id
_resolve_group_specs = resolve_group_specs


def _launch_plan_signature(plan: ControlPlaneLaunchPlan) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    return tuple(
        (
            group.group_name,
            group.workers,
            tuple(group.nodes),
        )
        for group in plan.groups
    )


__all__ = [
    "ControlPlaneConfigApplyBarrierNode",
    "ControlPlaneDagAssemblyNode",
    "ControlPlaneDiscoveryApplyNode",
    "ControlPlaneDiscoveryFinalizeNode",
    "ControlPlaneDiscoveryPumpNode",
    "ControlPlaneInitPlanNode",
    "ControlPlaneNodeConfigApplyNode",
    "ControlPlaneObservabilityConfigApplyNode",
    "ControlPlaneRootLeafBoundaryDispatchNode",
    "ControlPlaneRootLeafBoundaryResultNode",
    "ControlPlaneRootConfigStreamNode",
    "ControlPlaneStartupBarrierNode",
    "ControlPlaneRootLeafConfigAckNode",
    "ControlPlaneRootLeafConfigAssignNode",
    "ControlPlaneRootLeafStopAckNode",
    "ControlPlaneRootLeafStopDispatchNode",
    "ControlPlaneRootBootstrapNode",
    "ControlPlaneSystemConfigApplyNode",
]
