from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.kernel.node_annotation import node
from stream_kernel.observability.events import (
    DebugDispatchEvent,
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    MonitoringMetricsSnapshotEvent,
    TraceDispatchEvent,
    WorkerQueueTelemetryEvent,
)
from stream_kernel.routing.envelope import Envelope

from .ipc_handoff_dispatch_service import (
    DefaultExecutionIpcHandoffDispatchService,
)

OBSERVABILITY_HANDOFF_NODE_NAME = "system.transport.handoff.observability_dispatch"
OBSERVABILITY_TRACE_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.trace_bypass"
OBSERVABILITY_LOG_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.log_bypass"
OBSERVABILITY_DEBUG_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.debug_bypass"
OBSERVABILITY_METRIC_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.metric_bypass"
OBSERVABILITY_MONITOR_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.monitor_bypass"
OBSERVABILITY_MONITORING_METRICS_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.monitoring_metrics_bypass"
OBSERVABILITY_WORKER_QUEUE_HANDOFF_BYPASS_NODE_NAME = "system.transport.handoff.worker_queue_bypass"
OBSERVABILITY_TRACE_HANDOFF_NODE_NAME = "system.transport.handoff.trace_dispatch"
OBSERVABILITY_LOG_HANDOFF_NODE_NAME = "system.transport.handoff.log_dispatch"
OBSERVABILITY_DEBUG_HANDOFF_NODE_NAME = "system.transport.handoff.debug_dispatch"
OBSERVABILITY_METRIC_HANDOFF_NODE_NAME = "system.transport.handoff.metric_dispatch"
OBSERVABILITY_MONITOR_HANDOFF_NODE_NAME = "system.transport.handoff.monitor_dispatch"
OBSERVABILITY_MONITORING_METRICS_HANDOFF_NODE_NAME = "system.transport.handoff.monitoring_metrics_dispatch"
OBSERVABILITY_WORKER_QUEUE_HANDOFF_NODE_NAME = "system.transport.handoff.worker_queue_dispatch"

_OBSERVABILITY_EVENT_TARGETS: dict[type[object], str] = {
    TraceDispatchEvent: "system.obs.trace_dispatch",
    LogDispatchEvent: "system.obs.log_dispatch",
    DebugDispatchEvent: "system.obs.debug_dispatch",
    MetricDispatchEvent: "system.obs.metric_dispatch",
    MonitorDispatchEvent: "system.obs.monitor_dispatch",
    MonitoringMetricsSnapshotEvent: "system.obs.monitoring_metrics_dispatch",
    WorkerQueueTelemetryEvent: "system.obs.worker_queue_dispatch",
}

_OBSERVABILITY_EVENT_NODE_NAMES: dict[type[object], str] = {
    TraceDispatchEvent: OBSERVABILITY_TRACE_HANDOFF_NODE_NAME,
    LogDispatchEvent: OBSERVABILITY_LOG_HANDOFF_NODE_NAME,
    DebugDispatchEvent: OBSERVABILITY_DEBUG_HANDOFF_NODE_NAME,
    MetricDispatchEvent: OBSERVABILITY_METRIC_HANDOFF_NODE_NAME,
    MonitorDispatchEvent: OBSERVABILITY_MONITOR_HANDOFF_NODE_NAME,
    MonitoringMetricsSnapshotEvent: OBSERVABILITY_MONITORING_METRICS_HANDOFF_NODE_NAME,
    WorkerQueueTelemetryEvent: OBSERVABILITY_WORKER_QUEUE_HANDOFF_NODE_NAME,
}

_OBSERVABILITY_EVENT_BYPASS_NODE_NAMES: dict[type[object], str] = {
    TraceDispatchEvent: OBSERVABILITY_TRACE_HANDOFF_BYPASS_NODE_NAME,
    LogDispatchEvent: OBSERVABILITY_LOG_HANDOFF_BYPASS_NODE_NAME,
    DebugDispatchEvent: OBSERVABILITY_DEBUG_HANDOFF_BYPASS_NODE_NAME,
    MetricDispatchEvent: OBSERVABILITY_METRIC_HANDOFF_BYPASS_NODE_NAME,
    MonitorDispatchEvent: OBSERVABILITY_MONITOR_HANDOFF_BYPASS_NODE_NAME,
    MonitoringMetricsSnapshotEvent: OBSERVABILITY_MONITORING_METRICS_HANDOFF_BYPASS_NODE_NAME,
    WorkerQueueTelemetryEvent: OBSERVABILITY_WORKER_QUEUE_HANDOFF_BYPASS_NODE_NAME,
}


@node(
    name="system.transport.handoff.ipc_dispatch",
    consumes=[Envelope],
    emits=[],
)
@dataclass(slots=True)
class IpcHandoffDispatchNode:
    dispatch_service: DefaultExecutionIpcHandoffDispatchService = inject.service(
        DefaultExecutionIpcHandoffDispatchService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        if not isinstance(msg, Envelope):
            return []
        self.dispatch_service.dispatch_envelope(msg, source_group=None)
        return []


@node(
    name=OBSERVABILITY_HANDOFF_NODE_NAME,
    consumes=list(_OBSERVABILITY_EVENT_TARGETS.keys()),
    emits=[],
)
@dataclass(slots=True)
class IpcObservabilityHandoffDispatchNode:
    dispatch_service: DefaultExecutionIpcHandoffDispatchService = inject.service(
        DefaultExecutionIpcHandoffDispatchService
    )

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        target = _resolve_observability_target(payload)
        if not isinstance(target, str) or not target:
            return []
        source_group = _source_group_from_ctx(ctx)
        self.dispatch_service.dispatch_envelope(
            Envelope(payload=payload, target=target),
            source_group=source_group,
        )
        return []


@dataclass(slots=True)
class IpcTypedObservabilityHandoffDispatchNode:
    token: type[object]
    target: str
    dispatch_service: DefaultExecutionIpcHandoffDispatchService = inject.service(
        DefaultExecutionIpcHandoffDispatchService
    )

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, self.token):
            return []
        source_group = _source_group_from_ctx(ctx)
        self.dispatch_service.dispatch_envelope(
            Envelope(payload=payload, target=self.target),
            source_group=source_group,
        )
        return []


@dataclass(slots=True)
class IpcTypedObservabilityHandoffBypassNode:
    token: type[object]
    dispatch_node_target: str

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, self.token):
            return []
        trace_id = payload.trace_id if isinstance(getattr(payload, "trace_id", None), str) else None
        return [Envelope(payload=payload, target=self.dispatch_node_target, trace_id=trace_id)]


def build_transport_observability_handoff_plan(
    *,
    scenario_scope: object | None = None,
    enabled_tokens: list[type[object]] | tuple[type[object], ...] | None = None,
) -> tuple[list[StepSpec], dict[type[object], list[str]], set[str]]:
    tokens = _resolve_enabled_observability_tokens(enabled_tokens)
    steps: list[StepSpec] = []
    consumers: dict[type[object], list[str]] = {}
    node_names: set[str] = set()
    for token in tokens:
        target = _OBSERVABILITY_EVENT_TARGETS.get(token)
        node_name = _OBSERVABILITY_EVENT_NODE_NAMES.get(token)
        if not isinstance(target, str) or not target:
            continue
        if not isinstance(node_name, str) or not node_name:
            continue
        node = IpcTypedObservabilityHandoffDispatchNode(token=token, target=target)
        if scenario_scope is not None:
            apply_injection(node, scenario_scope, False)
            if node.dispatch_service is None:
                node.dispatch_service = inject.service(DefaultExecutionIpcHandoffDispatchService)
        steps.append(StepSpec(name=node_name, step=node))
        consumers[token] = [node_name]
        node_names.add(node_name)
    # Backward compatibility: when no explicit token set was requested,
    # keep legacy node name addressable by discovery.
    if not steps and enabled_tokens is None:
        legacy_node = IpcObservabilityHandoffDispatchNode()
        if scenario_scope is not None:
            apply_injection(legacy_node, scenario_scope, False)
            if legacy_node.dispatch_service is None:
                legacy_node.dispatch_service = inject.service(DefaultExecutionIpcHandoffDispatchService)
        return (
            [StepSpec(name=OBSERVABILITY_HANDOFF_NODE_NAME, step=legacy_node)],
            {token: [OBSERVABILITY_HANDOFF_NODE_NAME] for token in _OBSERVABILITY_EVENT_TARGETS},
            {OBSERVABILITY_HANDOFF_NODE_NAME},
        )
    return (steps, consumers, node_names)


def _resolve_enabled_observability_tokens(
    enabled_tokens: list[type[object]] | tuple[type[object], ...] | None,
) -> list[type[object]]:
    if enabled_tokens is None:
        return list(_OBSERVABILITY_EVENT_TARGETS.keys())
    ordered: list[type[object]] = []
    seen: set[type[object]] = set()
    for token in enabled_tokens:
        if token in seen:
            continue
        if token not in _OBSERVABILITY_EVENT_TARGETS:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _resolve_observability_target(payload: object) -> str | None:
    for token, target in _OBSERVABILITY_EVENT_TARGETS.items():
        if isinstance(payload, token):
            return target
    return None


def _source_group_from_ctx(ctx: object | None) -> str | None:
    if not isinstance(ctx, dict):
        return None
    source_group = ctx.get("__process_group")
    if isinstance(source_group, str) and source_group:
        return source_group
    return None


__all__ = [
    "IpcHandoffDispatchNode",
    "IpcObservabilityHandoffDispatchNode",
    "IpcTypedObservabilityHandoffDispatchNode",
    "IpcTypedObservabilityHandoffBypassNode",
    "OBSERVABILITY_HANDOFF_NODE_NAME",
    "OBSERVABILITY_TRACE_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_LOG_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_DEBUG_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_METRIC_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_MONITOR_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_MONITORING_METRICS_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_WORKER_QUEUE_HANDOFF_BYPASS_NODE_NAME",
    "OBSERVABILITY_TRACE_HANDOFF_NODE_NAME",
    "OBSERVABILITY_LOG_HANDOFF_NODE_NAME",
    "OBSERVABILITY_DEBUG_HANDOFF_NODE_NAME",
    "OBSERVABILITY_METRIC_HANDOFF_NODE_NAME",
    "OBSERVABILITY_MONITOR_HANDOFF_NODE_NAME",
    "OBSERVABILITY_MONITORING_METRICS_HANDOFF_NODE_NAME",
    "OBSERVABILITY_WORKER_QUEUE_HANDOFF_NODE_NAME",
    "build_transport_observability_handoff_plan",
]
