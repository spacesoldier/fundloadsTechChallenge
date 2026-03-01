from __future__ import annotations

from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.file_io import (
    egress_file_sink,
    ingress_file_source,
    sink_file_sink,
    source_file_source,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    execution_ipc_inmemory_adapter,
    execution_ipc_pipe_adapter,
)
from stream_kernel.platform.services.state.context import kv_store_memory


def test_file_adapters_are_marked_async_execution_mode() -> None:
    # File ingress/egress adapters are platform IO boundaries and must declare async capability.
    ingress_meta = get_adapter_meta(ingress_file_source)
    source_meta = get_adapter_meta(source_file_source)
    egress_meta = get_adapter_meta(egress_file_sink)
    sink_meta = get_adapter_meta(sink_file_sink)
    assert ingress_meta is not None
    assert source_meta is not None
    assert egress_meta is not None
    assert sink_meta is not None
    assert ingress_meta.execution_mode == "async"
    assert source_meta.execution_mode == "async"
    assert egress_meta.execution_mode == "async"
    assert sink_meta.execution_mode == "async"


def test_memory_kv_adapter_is_marked_sync_execution_mode() -> None:
    # In-memory KV adapter is CPU/memory-local and should stay sync-capable.
    kv_meta = get_adapter_meta(kv_store_memory)
    assert kv_meta is not None
    assert kv_meta.execution_mode == "sync"


def test_ipc_transport_adapters_are_marked_async_execution_mode() -> None:
    # IPC transport adapters should declare async capability by default.
    inmemory_meta = get_adapter_meta(execution_ipc_inmemory_adapter)
    pipe_meta = get_adapter_meta(execution_ipc_pipe_adapter)
    assert inmemory_meta is not None
    assert pipe_meta is not None
    assert inmemory_meta.execution_mode == "async"
    assert pipe_meta.execution_mode == "async"


def test_trace_sink_port_is_runtime_checkable_protocol() -> None:
    # TraceSinkPort must be a runtime-checkable Protocol so isinstance() works at wiring time.
    import inspect
    from stream_kernel.adapters.contracts import TraceSinkPort
    assert inspect.isclass(TraceSinkPort)
    sink = type("S", (), {
        "emit": lambda *a: None,
        "flush": lambda *a: None,
        "close": lambda *a: None,
    })()
    assert isinstance(sink, TraceSinkPort)


def test_trace_sink_adapters_declare_execution_mode() -> None:
    # Every trace sink adapter factory must carry @adapter metadata with a valid execution_mode.
    from stream_kernel.adapters.contracts import get_adapter_meta
    from stream_kernel.adapters.trace_sinks import (
        trace_jsonl_adapter,
        trace_otel_otlp_adapter,
        trace_otel_otlp_async_adapter,
        trace_opentracing_bridge_adapter,
        trace_stdout_adapter,
    )
    sync_factories = (
        trace_jsonl_adapter,
        trace_stdout_adapter,
        trace_otel_otlp_adapter,
        trace_opentracing_bridge_adapter,
    )
    for factory in (*sync_factories, trace_otel_otlp_async_adapter):
        meta = get_adapter_meta(factory)
        assert meta is not None, f"{factory.__name__} must have @adapter metadata"
        assert meta.execution_mode in {"sync", "async"}, (
            f"{factory.__name__}.execution_mode must be sync or async"
        )
    assert get_adapter_meta(trace_otel_otlp_async_adapter).execution_mode == "async"
    for factory in sync_factories:
        assert get_adapter_meta(factory).execution_mode == "sync", (
            f"{factory.__name__} must be sync"
        )


def test_async_trace_adapter_triggers_async_pool_via_plan_pools() -> None:
    # An async trace adapter binding must cause plan_pools() to assign the node to async pool.
    from dataclasses import dataclass
    from stream_kernel.adapters.contracts import TraceSinkPort
    from stream_kernel.adapters.registry import AdapterRegistry
    from stream_kernel.adapters.trace_sinks import trace_otel_otlp_async_adapter
    from stream_kernel.adapters.wiring import build_injection_registry
    from stream_kernel.application_context.inject import inject
    from stream_kernel.execution.runtime.planning import plan_pools

    @dataclass
    class _TraceSinkNode:
        sink: object = inject.stream(TraceSinkPort)

    registry = AdapterRegistry()
    registry.register("trace_sink", "otel_otlp_async", trace_otel_otlp_async_adapter)
    adapters_cfg = {"trace_sink": {"kind": "otel_otlp_async", "settings": {"endpoint": "http://localhost:4318"}}}
    bindings: dict[str, object] = {"trace_sink": ("stream", TraceSinkPort)}
    injection = build_injection_registry(adapters_cfg, registry, bindings)

    node = _TraceSinkNode()
    pools = plan_pools({"trace_sink_node": node}, injection)
    assert pools["trace_sink_node"] == "async"
