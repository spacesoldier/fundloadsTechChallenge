from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from stream_kernel.adapters.contracts import TraceSinkPort
from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.kernel.node_annotation import node
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.observability.events import (
    DebugDispatchEvent,
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    MonitoringMetricsSnapshotEvent,
    MonitoringMetricsSnapshotResult,
    TraceDispatchEvent,
    WorkerQueueTelemetryEvent,
)
from stream_kernel.platform.services.observability import (
    ObservabilityMetricsDispatchService,
    ObservabilityPipelineService,
    WorkerQueueTelemetryService,
    coerce_pipeline_observability,
)
from stream_kernel.platform.services.observability_debug import (
    NoOpRuntimeDebugDispatchService,
    RuntimeDebugDispatchService,
)
from stream_kernel.execution.transport.handoff.system_nodes import (
    OBSERVABILITY_HANDOFF_NODE_NAME,
    build_transport_observability_handoff_plan,
)
from stream_kernel.routing.envelope import Envelope

_SYSTEM_NODE_KIND_TO_EVENT: dict[str, type[object]] = {}


_SYSTEM_NODE_KIND_TO_EVENT = {
    "system.obs.trace_dispatch": TraceDispatchEvent,
    "system.obs.log_dispatch": LogDispatchEvent,
    "system.obs.debug_dispatch": DebugDispatchEvent,
    "system.obs.metric_dispatch": MetricDispatchEvent,
    "system.obs.monitor_dispatch": MonitorDispatchEvent,
    "system.obs.monitoring_metrics_dispatch": MonitoringMetricsSnapshotEvent,
    "system.obs.worker_queue_dispatch": WorkerQueueTelemetryEvent,
}

# ---------------------------------------------------------------------------
# Platform-rail dispatch nodes — each decorated with @node for registry scan
# and plan_pools() async-capability inspection.
# ---------------------------------------------------------------------------


@node(name="system.obs.trace_dispatch", consumes=[TraceDispatchEvent], emits=[])
@dataclass
class TraceDispatchNode:
    pipeline: ObservabilityPipelineService
    qualifier: str | None = None
    # Inject marker in instance __dict__ so plan_pools() can detect async capability.
    _obs_marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._obs_marker = inject.service(ObservabilityPipelineService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, TraceDispatchEvent):
            return []
        submit_event = getattr(self.pipeline, "submit_trace_event", None)
        if callable(submit_event):
            try:
                return _coerce_outputs(
                    submit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        emit_event_async = getattr(self.pipeline, "emit_trace_event_async", None)
        if callable(emit_event_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(emit_event_async, payload)
        emit_event = getattr(self.pipeline, "emit_trace_event", None)
        if callable(emit_event):
            try:
                return _coerce_outputs(
                    emit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        publish_async = getattr(self.pipeline, "publish_trace_async", None)
        if callable(publish_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(publish_async, payload)
        self.pipeline.publish_trace(
            event=payload.payload,
            trace_id=payload.trace_id,
            attributes=dict(payload.attributes),
        )
        return []

    async def _publish_async(self, publish: Any, payload: TraceDispatchEvent) -> list[object]:
        try:
            result = publish(
                event=payload.payload,
                trace_id=payload.trace_id,
                attributes=dict(payload.attributes),
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return []
        return _coerce_outputs(result)


@node(name="system.obs.log_dispatch", consumes=[LogDispatchEvent], emits=[])
@dataclass
class LogDispatchNode:
    pipeline: ObservabilityPipelineService
    qualifier: str | None = None
    _obs_marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._obs_marker = inject.service(ObservabilityPipelineService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, LogDispatchEvent):
            return []
        submit_event = getattr(self.pipeline, "submit_log_event", None)
        if callable(submit_event):
            try:
                return _coerce_outputs(
                    submit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        emit_event_async = getattr(self.pipeline, "emit_log_event_async", None)
        if callable(emit_event_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(emit_event_async, payload)
        emit_event = getattr(self.pipeline, "emit_log_event", None)
        if callable(emit_event):
            try:
                return _coerce_outputs(
                    emit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        publish_async = getattr(self.pipeline, "publish_log_async", None)
        if callable(publish_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(publish_async, payload)
        self.pipeline.publish_log(
            event=payload.payload,
            trace_id=payload.trace_id,
            attributes=dict(payload.attributes),
        )
        return []

    async def _publish_async(self, publish: Any, payload: LogDispatchEvent) -> list[object]:
        try:
            result = publish(
                event=payload.payload,
                trace_id=payload.trace_id,
                attributes=dict(payload.attributes),
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return []
        return _coerce_outputs(result)


@node(name="system.obs.debug_dispatch", consumes=[DebugDispatchEvent], emits=[])
@dataclass
class DebugDispatchNode:
    service: RuntimeDebugDispatchService
    qualifier: str | None = None
    _marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._marker = inject.service(RuntimeDebugDispatchService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, DebugDispatchEvent):
            return []
        return _coerce_outputs(self.service.dispatch(event=payload))


@node(name="system.obs.metric_dispatch", consumes=[MetricDispatchEvent], emits=[])
@dataclass
class MetricDispatchNode:
    pipeline: ObservabilityPipelineService
    qualifier: str | None = None
    _obs_marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._obs_marker = inject.service(ObservabilityPipelineService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, MetricDispatchEvent):
            return []
        submit_event = getattr(self.pipeline, "submit_metric_event", None)
        if callable(submit_event):
            try:
                return _coerce_outputs(
                    submit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        emit_event_async = getattr(self.pipeline, "emit_metric_event_async", None)
        if callable(emit_event_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(emit_event_async, payload)
        emit_event = getattr(self.pipeline, "emit_metric_event", None)
        if callable(emit_event):
            try:
                return _coerce_outputs(
                    emit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        publish_async = getattr(self.pipeline, "publish_metric_async", None)
        if callable(publish_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(publish_async, payload)
        self.pipeline.publish_metric(
            event=payload.payload,
            trace_id=payload.trace_id,
            attributes=dict(payload.attributes),
        )
        return []

    async def _publish_async(self, publish: Any, payload: MetricDispatchEvent) -> list[object]:
        try:
            result = publish(
                event=payload.payload,
                trace_id=payload.trace_id,
                attributes=dict(payload.attributes),
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return []
        return _coerce_outputs(result)


@node(name="system.obs.monitor_dispatch", consumes=[MonitorDispatchEvent], emits=[])
@dataclass
class MonitorDispatchNode:
    pipeline: ObservabilityPipelineService
    qualifier: str | None = None
    _obs_marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._obs_marker = inject.service(ObservabilityPipelineService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, MonitorDispatchEvent):
            return []
        submit_event = getattr(self.pipeline, "submit_monitoring_event", None)
        if callable(submit_event):
            try:
                return _coerce_outputs(
                    submit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        emit_event_async = getattr(self.pipeline, "emit_monitoring_event_async", None)
        if callable(emit_event_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(emit_event_async, payload)
        emit_event = getattr(self.pipeline, "emit_monitoring_event", None)
        if callable(emit_event):
            try:
                return _coerce_outputs(
                    emit_event(
                        event=payload.payload,
                        trace_id=payload.trace_id,
                        attributes=dict(payload.attributes),
                    )
                )
            except Exception:
                return []
        publish_async = getattr(self.pipeline, "publish_monitoring_async", None)
        if callable(publish_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(publish_async, payload)
        self.pipeline.publish_monitoring(
            event=payload.payload,
            trace_id=payload.trace_id,
            attributes=dict(payload.attributes),
        )
        return []

    async def _publish_async(self, publish: Any, payload: MonitorDispatchEvent) -> list[object]:
        try:
            result = publish(
                event=payload.payload,
                trace_id=payload.trace_id,
                attributes=dict(payload.attributes),
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return []
        return _coerce_outputs(result)


@node(name="system.obs.monitoring_metrics_dispatch", consumes=[MonitoringMetricsSnapshotEvent], emits=[MonitoringMetricsSnapshotResult])
@dataclass
class MonitoringMetricsDispatchNode:
    service: ObservabilityMetricsDispatchService
    qualifier: str | None = None
    _metrics_marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._metrics_marker = inject.service(ObservabilityMetricsDispatchService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, MonitoringMetricsSnapshotEvent):
            return []
        result = self.service.dispatch_snapshot(event=payload)
        return [result] if isinstance(result, MonitoringMetricsSnapshotResult) else []


@node(name="system.obs.worker_queue_dispatch", consumes=[WorkerQueueTelemetryEvent], emits=[])
@dataclass
class WorkerQueueTelemetryDispatchNode:
    service: WorkerQueueTelemetryService
    qualifier: str | None = None
    _marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._marker = inject.service(WorkerQueueTelemetryService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, WorkerQueueTelemetryEvent):
            return []
        publish_async = getattr(self.service, "publish_sample_async", None)
        if callable(publish_async):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return self._publish_async(publish_async, payload)
        self.service.publish_sample(sample=payload)
        return []

    async def _publish_async(self, publish: Any, payload: WorkerQueueTelemetryEvent) -> list[object]:
        try:
            result = publish(sample=payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return []
        return []


@node(name="system.obs.trace_sink", consumes=[], emits=[])
@dataclass
class TraceSinkNode:
    # inject.stream(TraceSinkPort) is resolved by the DI layer at wiring time.
    # The inject marker enables plan_pools() to detect async capability.
    sink: object = inject.stream(TraceSinkPort)

    def __call__(self, msg: object, _ctx: object | None) -> object:
        record = msg.payload if isinstance(msg, Envelope) else msg
        emit_async_fn = getattr(self.sink, "emit_async", None)
        if callable(emit_async_fn):
            # Return coroutine — AsyncRunner awaits it via _coerce_node_outputs.
            return self._async_emit(emit_async_fn, record)
        emit = getattr(self.sink, "emit", None)
        if callable(emit):
            emit(record)
        return []

    async def _async_emit(self, fn: Any, record: object) -> list[object]:
        await fn(record)
        return []


@dataclass(slots=True)
class _NoOpObservabilityMetricsDispatchService(ObservabilityMetricsDispatchService):
    def dispatch_snapshot(
        self,
        *,
        event: MonitoringMetricsSnapshotEvent,
    ) -> MonitoringMetricsSnapshotResult:
        return MonitoringMetricsSnapshotResult(
            stage=event.stage,
            snapshot=dict(event.snapshot),
            metric_records=[],
        )


@dataclass(slots=True)
class _NoOpWorkerQueueTelemetryService(WorkerQueueTelemetryService):
    def publish_sample(self, *, sample: WorkerQueueTelemetryEvent) -> None:
        _ = sample
        return None

    async def publish_sample_async(self, *, sample: WorkerQueueTelemetryEvent) -> None:
        _ = sample
        return None


# Mapping used by build_observability_system_plan() to select the concrete class per kind.
_KIND_TO_NODE_CLS: dict[str, type] = {
    "system.obs.trace_dispatch": TraceDispatchNode,
    "system.obs.log_dispatch": LogDispatchNode,
    "system.obs.debug_dispatch": DebugDispatchNode,
    "system.obs.metric_dispatch": MetricDispatchNode,
    "system.obs.monitor_dispatch": MonitorDispatchNode,
    "system.obs.monitoring_metrics_dispatch": MonitoringMetricsDispatchNode,
    "system.obs.worker_queue_dispatch": WorkerQueueTelemetryDispatchNode,
}


@dataclass(frozen=True, slots=True)
class ObservabilitySystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


def build_observability_system_plan(
    *,
    runtime: dict[str, object] | None,
    scenario_scope: ScenarioScope,
) -> ObservabilitySystemPlan:
    if not isinstance(runtime, dict):
        return ObservabilitySystemPlan()
    observability = runtime.get("observability")
    if not isinstance(observability, dict):
        return ObservabilitySystemPlan()
    nodes_cfg = _resolve_system_nodes_config(observability)
    if not nodes_cfg:
        return ObservabilitySystemPlan()
    if _is_observability_root_transport_only(runtime):
        process_role = runtime.get("__process_role")
        enabled_tokens: list[type[Any]] = []
        enabled_token_set: set[type[Any]] = set()
        kind_counts: dict[str, int] = {}
        for cfg in nodes_cfg:
            if not isinstance(cfg, dict):
                continue
            kind = cfg.get("kind")
            if not isinstance(kind, str) or not kind or kind not in _SYSTEM_NODE_KIND_TO_EVENT:
                continue
            enabled = cfg.get("enabled", True)
            if not isinstance(enabled, bool) or not enabled:
                continue
            qualifier = cfg.get("qualifier")
            if not isinstance(qualifier, str) or not qualifier:
                qualifier = None
            suffix_index = kind_counts.get(kind, 0)
            kind_counts[kind] = suffix_index + 1
            _ = _build_system_node_name(kind=kind, qualifier=qualifier, index=suffix_index)
            token = _SYSTEM_NODE_KIND_TO_EVENT[kind]
            if token in enabled_token_set:
                continue
            enabled_token_set.add(token)
            enabled_tokens.append(token)
        if not enabled_tokens:
            return ObservabilitySystemPlan()
        _ = process_role
        handoff_steps, handoff_consumers, handoff_nodes = build_transport_observability_handoff_plan(
            scenario_scope=scenario_scope,
            enabled_tokens=enabled_tokens,
        )
        consumers: dict[type[Any], list[str]] = {}
        for token in enabled_tokens:
            mapped = handoff_consumers.get(token)
            if isinstance(mapped, list) and mapped:
                consumers[token] = list(mapped)
            else:
                consumers[token] = [_legacy_system_obs_node_name_for_token(token)]
        return ObservabilitySystemPlan(
            system_steps=list(handoff_steps),
            system_consumers=consumers,
            system_node_names=set(handoff_nodes) if handoff_nodes else {OBSERVABILITY_HANDOFF_NODE_NAME},
        )

    steps: list[StepSpec] = []
    consumers: dict[type[Any], list[str]] = {}
    node_names: set[str] = set()
    kind_counts: dict[str, int] = {}
    for cfg in nodes_cfg:
        if not isinstance(cfg, dict):
            continue
        kind = cfg.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        if kind not in _SYSTEM_NODE_KIND_TO_EVENT:
            continue
        enabled = cfg.get("enabled", True)
        if not isinstance(enabled, bool) or not enabled:
            continue
        qualifier = cfg.get("qualifier")
        if not isinstance(qualifier, str) or not qualifier:
            qualifier = None
        suffix_index = kind_counts.get(kind, 0)
        kind_counts[kind] = suffix_index + 1
        node_name = _build_system_node_name(
            kind=kind,
            qualifier=qualifier,
            index=suffix_index,
        )
        node_cls = _KIND_TO_NODE_CLS[kind]
        dispatch_node = _build_system_dispatch_node(
            kind=kind,
            node_cls=node_cls,
            qualifier=qualifier,
        )
        apply_injection(dispatch_node, scenario_scope, False)
        _finalize_observability_dispatch_node(dispatch_node=dispatch_node, kind=kind)
        _restore_observability_plan_marker(dispatch_node=dispatch_node, kind=kind, qualifier=qualifier)
        steps.append(StepSpec(name=node_name, step=dispatch_node))
        consumers.setdefault(_SYSTEM_NODE_KIND_TO_EVENT[kind], []).append(node_name)
        node_names.add(node_name)
    return ObservabilitySystemPlan(
        system_steps=steps,
        system_consumers=consumers,
        system_node_names=node_names,
    )


def _is_observability_root_transport_only(runtime: dict[str, object]) -> bool:
    role = runtime.get("__process_role")
    if isinstance(role, str) and role == "observability_worker":
        return False
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return False
    bootstrap = platform.get("bootstrap", {})
    if not isinstance(bootstrap, dict) or bootstrap.get("mode") != "process_supervisor":
        return False
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict) or not service_process:
        service_process = observability.get("service_worker", {})
        if not isinstance(service_process, dict):
            return False
    return service_process.get("enabled") is True


def _resolve_system_nodes_config(observability: dict[str, object]) -> list[dict[str, object]]:
    # External config contract stays exporter-based. If no explicit pipeline.system_nodes
    # is declared, derive default dispatch nodes from enabled exporter channels.
    pipeline_cfg = observability.get("pipeline")
    if isinstance(pipeline_cfg, dict):
        nodes_cfg = pipeline_cfg.get("system_nodes")
        if isinstance(nodes_cfg, list):
            return [node for node in nodes_cfg if isinstance(node, dict)]

    service_process_cfg = observability.get("service_process")
    if not isinstance(service_process_cfg, dict):
        service_process_cfg = observability.get("service_worker")
    service_process_nodes: list[str] = []
    if isinstance(service_process_cfg, dict):
        configured_nodes = service_process_cfg.get("nodes")
        if isinstance(configured_nodes, list):
            service_process_nodes = [
                node_name
                for node_name in configured_nodes
                if isinstance(node_name, str) and node_name in _SYSTEM_NODE_KIND_TO_EVENT
            ]

    nodes: list[dict[str, object]] = []
    for node_name in service_process_nodes:
        nodes.append({"kind": node_name, "enabled": True})

    if _has_enabled_exporters(observability, "tracing"):
        nodes.append({"kind": "system.obs.trace_dispatch", "enabled": True})
    if _has_enabled_exporters(observability, "logging"):
        nodes.append({"kind": "system.obs.log_dispatch", "enabled": True})
    if _has_enabled_exporter_kind(observability, "logging", "redis_debug"):
        nodes.append({"kind": "system.obs.debug_dispatch", "enabled": True})
    if _has_enabled_exporters(observability, "telemetry"):
        nodes.append({"kind": "system.obs.metric_dispatch", "enabled": True})
    if _has_enabled_exporters(observability, "monitoring"):
        nodes.append({"kind": "system.obs.monitor_dispatch", "enabled": True})
        nodes.append({"kind": "system.obs.monitoring_metrics_dispatch", "enabled": True})
    queue_telemetry_cfg = observability.get("worker_queue_telemetry", {})
    if isinstance(queue_telemetry_cfg, dict) and queue_telemetry_cfg.get("enabled") is True:
        nodes.append({"kind": "system.obs.worker_queue_dispatch", "enabled": True})
    unique: list[dict[str, object]] = []
    seen: set[str] = set()
    for node in nodes:
        kind = node.get("kind")
        if not isinstance(kind, str) or kind in seen:
            continue
        seen.add(kind)
        unique.append(node)
    return unique


def _has_enabled_exporters(observability: dict[str, object], section: str) -> bool:
    section_cfg = observability.get(section)
    if not isinstance(section_cfg, dict):
        return False
    exporters = section_cfg.get("exporters")
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(item, dict) and item.get("enabled", True) is not False
        for item in exporters
    )


def _has_enabled_exporter_kind(observability: dict[str, object], section: str, kind: str) -> bool:
    section_cfg = observability.get(section)
    if not isinstance(section_cfg, dict):
        return False
    exporters = section_cfg.get("exporters")
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("enabled", True) is not False
        and item.get("kind") == kind
        for item in exporters
    )


def _coerce_outputs(candidate: object) -> list[object]:
    if candidate is None:
        return []
    if isinstance(candidate, list):
        return list(candidate)
    if isinstance(candidate, tuple):
        return list(candidate)
    return [candidate]


def _build_system_dispatch_node(
    *,
    kind: str,
    node_cls: type[Any],
    qualifier: str | None,
) -> object:
    if kind in {
        "system.obs.trace_dispatch",
        "system.obs.log_dispatch",
        "system.obs.metric_dispatch",
        "system.obs.monitor_dispatch",
    }:
        return node_cls(
            pipeline=inject.service(ObservabilityPipelineService, qualifier=qualifier),
            qualifier=qualifier,
        )
    if kind == "system.obs.debug_dispatch":
        return node_cls(
            service=inject.service(RuntimeDebugDispatchService, qualifier=qualifier),
            qualifier=qualifier,
        )
    if kind == "system.obs.monitoring_metrics_dispatch":
        return node_cls(
            service=inject.service(ObservabilityMetricsDispatchService, qualifier=qualifier),
            qualifier=qualifier,
        )
    if kind == "system.obs.worker_queue_dispatch":
        return node_cls(
            service=inject.service(WorkerQueueTelemetryService, qualifier=qualifier),
            qualifier=qualifier,
        )
    raise ValueError(f"unsupported observability system node kind: {kind}")


def _restore_observability_plan_marker(
    *,
    dispatch_node: object,
    kind: str,
    qualifier: str | None,
) -> None:
    marker: object | None = None
    if kind in {
        "system.obs.trace_dispatch",
        "system.obs.log_dispatch",
        "system.obs.metric_dispatch",
        "system.obs.monitor_dispatch",
    }:
        marker = inject.service(ObservabilityPipelineService, qualifier=qualifier)
        setattr(dispatch_node, "_obs_marker", marker)
        return
    if kind == "system.obs.debug_dispatch":
        marker = inject.service(RuntimeDebugDispatchService, qualifier=qualifier)
        setattr(dispatch_node, "_marker", marker)
        return
    if kind == "system.obs.monitoring_metrics_dispatch":
        marker = inject.service(ObservabilityMetricsDispatchService, qualifier=qualifier)
        setattr(dispatch_node, "_metrics_marker", marker)
        return
    if kind == "system.obs.worker_queue_dispatch":
        marker = inject.service(WorkerQueueTelemetryService, qualifier=qualifier)
        setattr(dispatch_node, "_marker", marker)


def _finalize_observability_dispatch_node(*, dispatch_node: object, kind: str) -> None:
    if kind in {
        "system.obs.trace_dispatch",
        "system.obs.log_dispatch",
        "system.obs.metric_dispatch",
        "system.obs.monitor_dispatch",
    }:
        if getattr(dispatch_node, "pipeline", None) is None:
            setattr(dispatch_node, "pipeline", coerce_pipeline_observability(None))
        return
    if kind == "system.obs.debug_dispatch":
        if getattr(dispatch_node, "service", None) is None:
            setattr(dispatch_node, "service", NoOpRuntimeDebugDispatchService())
        return
    if kind == "system.obs.monitoring_metrics_dispatch":
        if getattr(dispatch_node, "service", None) is None:
            setattr(dispatch_node, "service", _NoOpObservabilityMetricsDispatchService())
        return
    if kind == "system.obs.worker_queue_dispatch":
        if getattr(dispatch_node, "service", None) is None:
            setattr(dispatch_node, "service", _NoOpWorkerQueueTelemetryService())


def _build_system_node_name(*, kind: str, qualifier: str | None, index: int) -> str:
    if isinstance(qualifier, str) and qualifier:
        return f"{kind}:{qualifier}"
    if index == 0:
        return kind
    return f"{kind}:{index + 1}"


def _legacy_system_obs_node_name_for_token(token: type[Any]) -> str:
    for node_name, mapped_token in _SYSTEM_NODE_KIND_TO_EVENT.items():
        if mapped_token is token:
            return node_name
    return "system.obs.log_dispatch"
