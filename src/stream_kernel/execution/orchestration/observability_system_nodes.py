from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from stream_kernel.adapters.contracts import TraceSinkPort
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import (
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.kernel.node_annotation import node
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.observability.events import (
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    MonitoringMetricsSnapshotEvent,
    MonitoringMetricsSnapshotResult,
    TraceDispatchEvent,
)
from stream_kernel.platform.services.observability import (
    ObservabilityMetricsDispatchService,
    ObservabilityPipelineService,
    coerce_pipeline_observability,
)
from stream_kernel.routing.envelope import Envelope

_SYSTEM_NODE_KIND_TO_EVENT: dict[str, type[object]] = {}


_SYSTEM_NODE_KIND_TO_EVENT = {
    "system.obs.trace_dispatch": TraceDispatchEvent,
    "system.obs.log_dispatch": LogDispatchEvent,
    "system.obs.metric_dispatch": MetricDispatchEvent,
    "system.obs.monitor_dispatch": MonitorDispatchEvent,
    "system.obs.monitoring_metrics_dispatch": MonitoringMetricsSnapshotEvent,
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
                await result
        except Exception:
            return []
        return []


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
                await result
        except Exception:
            return []
        return []


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
                await result
        except Exception:
            return []
        return []


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
                await result
        except Exception:
            return []
        return []


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


# Mapping used by build_observability_system_plan() to select the concrete class per kind.
_KIND_TO_NODE_CLS: dict[str, type] = {
    "system.obs.trace_dispatch": TraceDispatchNode,
    "system.obs.log_dispatch": LogDispatchNode,
    "system.obs.metric_dispatch": MetricDispatchNode,
    "system.obs.monitor_dispatch": MonitorDispatchNode,
    "system.obs.monitoring_metrics_dispatch": MonitoringMetricsDispatchNode,
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
    # Multiprocess primitive mode: worker processes execute business nodes only;
    # observability dispatch/export is owned by supervisor.
    if runtime.get("__process_role") == "worker":
        return ObservabilitySystemPlan()
    observability = runtime.get("observability")
    if not isinstance(observability, dict):
        return ObservabilitySystemPlan()
    nodes_cfg = _resolve_system_nodes_config(observability)
    if not nodes_cfg:
        return ObservabilitySystemPlan()

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
        pipeline = _resolve_pipeline_service(
            scope=scenario_scope,
            qualifier=qualifier,
        )
        suffix_index = kind_counts.get(kind, 0)
        kind_counts[kind] = suffix_index + 1
        node_name = _build_system_node_name(
            kind=kind,
            qualifier=qualifier,
            index=suffix_index,
        )
        node_cls = _KIND_TO_NODE_CLS[kind]
        dispatch_node = node_cls(pipeline=pipeline, qualifier=qualifier)
        steps.append(StepSpec(name=node_name, step=dispatch_node))
        consumers.setdefault(_SYSTEM_NODE_KIND_TO_EVENT[kind], []).append(node_name)
        node_names.add(node_name)
    return ObservabilitySystemPlan(
        system_steps=steps,
        system_consumers=consumers,
        system_node_names=node_names,
    )


def _resolve_system_nodes_config(observability: dict[str, object]) -> list[dict[str, object]]:
    # External config contract stays exporter-based. If no explicit pipeline.system_nodes
    # is declared, derive default dispatch nodes from enabled exporter channels.
    pipeline_cfg = observability.get("pipeline")
    if isinstance(pipeline_cfg, dict):
        nodes_cfg = pipeline_cfg.get("system_nodes")
        if isinstance(nodes_cfg, list):
            return [node for node in nodes_cfg if isinstance(node, dict)]

    tracing_cfg = observability.get("tracing")
    if not isinstance(tracing_cfg, dict):
        return []
    exporters = tracing_cfg.get("exporters")
    if not isinstance(exporters, list):
        return []
    if not any(isinstance(item, dict) and item.get("enabled", True) is not False for item in exporters):
        return []
    return [{"kind": "system.obs.trace_dispatch", "enabled": True}]


def _resolve_pipeline_service(
    *, scope: ScenarioScope, qualifier: str | None
) -> ObservabilityPipelineService:
    try:
        if isinstance(qualifier, str):
            return coerce_pipeline_observability(
                scope.resolve("service", ObservabilityPipelineService, qualifier=qualifier)
            )
        return coerce_pipeline_observability(scope.resolve("service", ObservabilityPipelineService))
    except InjectionRegistryError:
        return coerce_pipeline_observability(None)


def _build_system_node_name(*, kind: str, qualifier: str | None, index: int) -> str:
    if isinstance(qualifier, str) and qualifier:
        return f"{kind}:{qualifier}"
    if index == 0:
        return kind
    return f"{kind}:{index + 1}"
