from __future__ import annotations


def test_trace_sink_node_is_in_node_registry() -> None:
    # Phase B: TraceSinkNode must be discoverable with @node(name="system.obs.trace_sink").
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode
    from stream_kernel.kernel.node import NodeMeta

    meta = getattr(TraceSinkNode, "__node_meta__", None)
    assert isinstance(meta, NodeMeta), "TraceSinkNode must have @node metadata"
    assert meta.name == "system.obs.trace_sink"


def test_trace_dispatch_nodes_are_in_node_registry() -> None:
    # Phase B: Four concrete dispatch node classes must each carry @node metadata with correct name.
    from stream_kernel.execution.orchestration.observability_system_nodes import (
        LogDispatchNode,
        MetricDispatchNode,
        MonitorDispatchNode,
        MonitoringMetricsDispatchNode,
        TraceDispatchNode,
    )
    from stream_kernel.kernel.node import NodeMeta

    expected = {
        TraceDispatchNode: "system.obs.trace_dispatch",
        LogDispatchNode: "system.obs.log_dispatch",
        MetricDispatchNode: "system.obs.metric_dispatch",
        MonitorDispatchNode: "system.obs.monitor_dispatch",
        MonitoringMetricsDispatchNode: "system.obs.monitoring_metrics_dispatch",
    }
    for cls, expected_name in expected.items():
        meta = getattr(cls, "__node_meta__", None)
        assert isinstance(meta, NodeMeta), f"{cls.__name__} must have @node metadata"
        assert meta.name == expected_name, f"{cls.__name__}.name mismatch"


def test_fanout_service_is_service_decorated() -> None:
    # Phase B: FanoutObservabilityService must be discoverable via @service for DI scan.
    from stream_kernel.application_context.service import ServiceMeta
    from stream_kernel.platform.services.observability import FanoutObservabilityService

    meta = getattr(FanoutObservabilityService, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta), "FanoutObservabilityService must have @service metadata"
    assert meta.name  # non-empty name


def test_reply_aware_service_is_service_decorated() -> None:
    # Phase B: ReplyAwareObservabilityService must be discoverable via @service for DI scan.
    from stream_kernel.application_context.service import ServiceMeta
    from stream_kernel.platform.services.observability import ReplyAwareObservabilityService

    meta = getattr(ReplyAwareObservabilityService, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta), "ReplyAwareObservabilityService must have @service metadata"
    assert meta.name


def test_trace_sink_node_async_capability_via_port() -> None:
    # Phase B: TraceSinkNode with async TraceSinkPort binding → plan_pools returns "async".
    from stream_kernel.adapters.contracts import TraceSinkPort
    from stream_kernel.adapters.registry import AdapterRegistry
    from stream_kernel.adapters.trace_sinks import trace_otel_otlp_async_adapter
    from stream_kernel.adapters.wiring import build_injection_registry
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode
    from stream_kernel.execution.runtime.planning import plan_pools

    registry = AdapterRegistry()
    registry.register("trace_sink", "otel_otlp_async", trace_otel_otlp_async_adapter)
    adapters_cfg = {
        "trace_sink": {"kind": "otel_otlp_async", "settings": {"endpoint": "http://localhost:4318"}}
    }
    bindings: dict[str, object] = {"trace_sink": ("stream", TraceSinkPort)}
    injection = build_injection_registry(adapters_cfg, registry, bindings)

    node = TraceSinkNode()
    pools = plan_pools({"system.obs.trace_sink": node}, injection)
    assert pools["system.obs.trace_sink"] == "async"
