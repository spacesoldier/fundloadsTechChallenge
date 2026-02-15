from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import (
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.platform.services.observability import (
    ObservabilityPipelineService,
    coerce_pipeline_observability,
)
from stream_kernel.routing.envelope import Envelope

_SYSTEM_NODE_KIND_TO_EVENT: dict[str, type[object]] = {}
_SYSTEM_NODE_KIND_TO_METHOD: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class TraceDispatchEvent:
    payload: object
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogDispatchEvent:
    payload: object
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MetricDispatchEvent:
    payload: object
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MonitorDispatchEvent:
    payload: object
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)


_SYSTEM_NODE_KIND_TO_EVENT = {
    "system.obs.trace_dispatch": TraceDispatchEvent,
    "system.obs.log_dispatch": LogDispatchEvent,
    "system.obs.metric_dispatch": MetricDispatchEvent,
    "system.obs.monitor_dispatch": MonitorDispatchEvent,
}

_SYSTEM_NODE_KIND_TO_METHOD = {
    "system.obs.trace_dispatch": "publish_trace",
    "system.obs.log_dispatch": "publish_log",
    "system.obs.metric_dispatch": "publish_metric",
    "system.obs.monitor_dispatch": "publish_monitoring",
}


@dataclass(frozen=True, slots=True)
class ObservabilitySystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


@dataclass
class ObservabilityDispatchNode:
    kind: str
    qualifier: str | None
    pipeline: ObservabilityPipelineService
    # Injection marker kept on-node for async planning (`plan_pools`) with qualifier support.
    observability: object = field(init=False)

    def __post_init__(self) -> None:
        self.observability = inject.service(ObservabilityPipelineService, qualifier=self.qualifier)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        event_type = _SYSTEM_NODE_KIND_TO_EVENT.get(self.kind)
        method_name = _SYSTEM_NODE_KIND_TO_METHOD.get(self.kind)
        if event_type is None or not isinstance(method_name, str):
            return []
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, event_type):
            return []
        method = getattr(self.pipeline, method_name, None)
        if not callable(method):
            return []
        method(
            event=payload.payload,
            trace_id=payload.trace_id,
            attributes=dict(payload.attributes),
        )
        return []


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
    pipeline_cfg = observability.get("pipeline")
    if not isinstance(pipeline_cfg, dict):
        return ObservabilitySystemPlan()
    nodes_cfg = pipeline_cfg.get("system_nodes")
    if not isinstance(nodes_cfg, list):
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
        node = ObservabilityDispatchNode(
            kind=kind,
            qualifier=qualifier,
            pipeline=pipeline,
        )
        steps.append(StepSpec(name=node_name, step=node))
        consumers.setdefault(_SYSTEM_NODE_KIND_TO_EVENT[kind], []).append(node_name)
        node_names.add(node_name)
    return ObservabilitySystemPlan(
        system_steps=steps,
        system_consumers=consumers,
        system_node_names=node_names,
    )


def _resolve_pipeline_service(*, scope: ScenarioScope, qualifier: str | None) -> ObservabilityPipelineService:
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
