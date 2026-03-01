from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.kernel.node_annotation import node
from stream_kernel.observability.events import (
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

_OBSERVABILITY_EVENT_TARGETS: dict[type[object], str] = {
    TraceDispatchEvent: "system.obs.trace_dispatch",
    LogDispatchEvent: "system.obs.log_dispatch",
    MetricDispatchEvent: "system.obs.metric_dispatch",
    MonitorDispatchEvent: "system.obs.monitor_dispatch",
    MonitoringMetricsSnapshotEvent: "system.obs.monitoring_metrics_dispatch",
    WorkerQueueTelemetryEvent: "system.obs.worker_queue_dispatch",
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


def build_transport_observability_handoff_plan(
    *,
    scenario_scope: object | None = None,
) -> tuple[list[StepSpec], dict[type[object], list[str]], set[str]]:
    dispatch_service = _resolve_dispatch_service_from_scope(scenario_scope)
    node = (
        IpcObservabilityHandoffDispatchNode(dispatch_service=dispatch_service)
        if dispatch_service is not None
        else IpcObservabilityHandoffDispatchNode()
    )
    step = StepSpec(name=OBSERVABILITY_HANDOFF_NODE_NAME, step=node)
    consumers = {token: [OBSERVABILITY_HANDOFF_NODE_NAME] for token in _OBSERVABILITY_EVENT_TARGETS}
    return ([step], consumers, {OBSERVABILITY_HANDOFF_NODE_NAME})


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


def _resolve_dispatch_service_from_scope(
    scenario_scope: object | None,
) -> DefaultExecutionIpcHandoffDispatchService | None:
    if scenario_scope is None:
        return None
    resolve = getattr(scenario_scope, "resolve", None)
    if not callable(resolve):
        return None
    try:
        resolved = resolve("service", DefaultExecutionIpcHandoffDispatchService)
    except Exception:
        return None
    if isinstance(resolved, DefaultExecutionIpcHandoffDispatchService):
        return resolved
    if callable(getattr(resolved, "dispatch_envelope", None)):
        return resolved  # type: ignore[return-value]
    return None


__all__ = [
    "IpcHandoffDispatchNode",
    "IpcObservabilityHandoffDispatchNode",
    "OBSERVABILITY_HANDOFF_NODE_NAME",
    "build_transport_observability_handoff_plan",
]
