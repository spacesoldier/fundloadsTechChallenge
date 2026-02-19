from __future__ import annotations

from pathlib import Path

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.trace_sinks import (
    JsonlTraceSink,
    NoOpTraceSink,
    OpenTracingBridgeTraceSink,
    OTelOtlpTraceSink,
    StdoutTraceSink,
    check_otel_backend_dependencies,
)
from stream_kernel.observability.domain.tracing import TraceMessage


def _flatten_trace_otel_grouped_settings(settings: dict[str, object]) -> dict[str, object]:
    # Grouped config syntax is supported in parallel with flat keys.
    normalized = dict(settings)
    otlp = normalized.get("otlp")
    if otlp is not None and not isinstance(otlp, dict):
        raise ValueError("trace_otel_otlp.settings.otlp must be a mapping when provided")
    transport = normalized.get("transport")
    if transport is not None and not isinstance(transport, dict):
        raise ValueError("trace_otel_otlp.settings.transport must be a mapping when provided")
    service = normalized.get("service")
    if service is not None and not isinstance(service, dict):
        raise ValueError("trace_otel_otlp.settings.service must be a mapping when provided")
    view = normalized.get("view")
    if view is not None and not isinstance(view, dict):
        raise ValueError("trace_otel_otlp.settings.view must be a mapping when provided")

    if isinstance(otlp, dict):
        if "endpoint" in otlp:
            normalized["endpoint"] = otlp["endpoint"]
        if "headers" in otlp:
            normalized["headers"] = otlp["headers"]

    if isinstance(transport, dict):
        for key in (
            "backend",
            "timeout_seconds",
            "dependency_missing",
            "bridge",
            "batch",
            "queue",
            "retry",
            "httpx",
            "grpc",
            "urllib3",
            "aiohttp",
        ):
            if key in transport:
                normalized[key] = transport[key]

    if isinstance(service, dict):
        for key in (
            "service_name",
            "service_namespace",
            "service_version",
            "service_instance_id",
            "deployment_environment",
        ):
            if key in service:
                normalized[key] = service[key]

    if isinstance(view, dict):
        for key in (
            "trace_view",
            "service_name_by_step",
            "service_name_by_process_group",
            "logical_include_platform_spans",
            "topology_include_business_spans",
            "service_name_suffix",
            "isolate_view_ids",
            "include_runtime_resource",
            "span_kind",
        ):
            if key in view:
                normalized[key] = view[key]

    return normalized


def _resolve_trace_otel_backend(settings: dict[str, object]) -> str:
    transport = settings.get("transport")
    if isinstance(transport, dict):
        backend = transport.get("backend")
        if isinstance(backend, str) and backend:
            return backend
    backend = settings.get("backend", "urllib")
    if not isinstance(backend, str) or not backend:
        raise ValueError("trace_otel_otlp.settings.backend must be a non-empty string when provided")
    return backend


@adapter(
    name="trace_stdout",
    consumes=[TraceMessage],
    emits=[],
    binds=[("kv_stream", TraceMessage)],
    execution_mode="async",
)
def trace_stdout(settings: dict[str, object]) -> StdoutTraceSink:
    # Framework-owned stdout trace sink adapter.
    _ = settings
    return StdoutTraceSink()


@adapter(
    name="trace_jsonl",
    consumes=[TraceMessage],
    emits=[],
    binds=[("kv_stream", TraceMessage)],
    execution_mode="async",
)
def trace_jsonl(settings: dict[str, object]) -> JsonlTraceSink:
    # Framework-owned JSONL trace sink adapter.
    path = settings.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("trace_jsonl.settings.path must be a non-empty string")
    trace_slice = settings.get("trace_slice", "all")
    view = settings.get("view")
    if isinstance(view, dict) and "trace_view" in view:
        trace_slice = view["trace_view"]
    elif "trace_view" in settings:
        trace_slice = settings.get("trace_view")
    if not isinstance(trace_slice, str) or not trace_slice:
        raise ValueError("trace_jsonl.settings.trace_slice must be a non-empty string when provided")
    return JsonlTraceSink(
        path=Path(path),
        write_mode=str(settings.get("write_mode", "line")),
        flush_every_n=int(settings.get("flush_every_n", 1)),
        flush_every_ms=settings.get("flush_every_ms") if isinstance(settings.get("flush_every_ms"), int) else None,
        fsync_every_n=settings.get("fsync_every_n") if isinstance(settings.get("fsync_every_n"), int) else None,
        trace_slice=trace_slice,
    )


@adapter(
    name="trace_otel_otlp",
    consumes=[TraceMessage],
    emits=[],
    binds=[("kv_stream", TraceMessage)],
    execution_mode="async",
)
def trace_otel_otlp(settings: dict[str, object]) -> OTelOtlpTraceSink | NoOpTraceSink:
    # Framework-owned OpenTelemetry OTLP trace exporter sink.
    settings = _flatten_trace_otel_grouped_settings(settings)
    backend = _resolve_trace_otel_backend(settings)
    dependency_missing = settings.get("dependency_missing", "error")
    if not isinstance(dependency_missing, str) or dependency_missing not in {"error", "degrade_noop"}:
        raise ValueError(
            "trace_otel_otlp.settings.dependency_missing must be one of: ['error', 'degrade_noop']"
        )
    try:
        check_otel_backend_dependencies(backend)
    except Exception as exc:
        if dependency_missing == "degrade_noop":
            return NoOpTraceSink(reason=f"trace_otel_otlp:{backend}:dependency_missing")
        raise ValueError(f"trace_otel_otlp dependency missing for backend '{backend}'") from exc
    endpoint = settings.get("endpoint", "http://127.0.0.1:4318/v1/traces")
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("trace_otel_otlp.settings.endpoint must be a non-empty string")
    headers_raw = settings.get("headers", {})
    if not isinstance(headers_raw, dict):
        raise ValueError("trace_otel_otlp.settings.headers must be a mapping when provided")
    headers: dict[str, str] = {}
    for key, value in headers_raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("trace_otel_otlp.settings.headers must be string-to-string mapping")
        headers[key] = value
    service_name = settings.get("service_name", "stream-kernel")
    if not isinstance(service_name, str) or not service_name:
        raise ValueError("trace_otel_otlp.settings.service_name must be a non-empty string")
    timeout_seconds = settings.get("timeout_seconds", 2.0)
    if not isinstance(timeout_seconds, (int, float)) or float(timeout_seconds) <= 0:
        raise ValueError("trace_otel_otlp.settings.timeout_seconds must be > 0 when provided")
    export_fn = settings.get("_export_fn")
    if export_fn is not None and not callable(export_fn):
        raise ValueError("trace_otel_otlp.settings._export_fn must be callable when provided")
    service_namespace = settings.get("service_namespace")
    if service_namespace is not None and (not isinstance(service_namespace, str) or not service_namespace):
        raise ValueError("trace_otel_otlp.settings.service_namespace must be a non-empty string when provided")
    service_version = settings.get("service_version")
    if service_version is not None and (not isinstance(service_version, str) or not service_version):
        raise ValueError("trace_otel_otlp.settings.service_version must be a non-empty string when provided")
    service_instance_id = settings.get("service_instance_id")
    if service_instance_id is not None and (not isinstance(service_instance_id, str) or not service_instance_id):
        raise ValueError("trace_otel_otlp.settings.service_instance_id must be a non-empty string when provided")
    deployment_environment = settings.get("deployment_environment")
    if deployment_environment is not None and (
        not isinstance(deployment_environment, str) or not deployment_environment
    ):
        raise ValueError("trace_otel_otlp.settings.deployment_environment must be a non-empty string when provided")
    service_name_by_process_group = settings.get("service_name_by_process_group", True)
    if not isinstance(service_name_by_process_group, bool):
        raise ValueError("trace_otel_otlp.settings.service_name_by_process_group must be a boolean when provided")
    service_name_by_step = settings.get("service_name_by_step", False)
    if not isinstance(service_name_by_step, bool):
        raise ValueError("trace_otel_otlp.settings.service_name_by_step must be a boolean when provided")
    service_name_suffix = settings.get("service_name_suffix")
    if service_name_suffix is not None and (not isinstance(service_name_suffix, str) or not service_name_suffix):
        raise ValueError("trace_otel_otlp.settings.service_name_suffix must be a non-empty string when provided")
    logical_include_platform_spans = settings.get("logical_include_platform_spans", False)
    if not isinstance(logical_include_platform_spans, bool):
        raise ValueError(
            "trace_otel_otlp.settings.logical_include_platform_spans must be a boolean when provided"
        )
    topology_include_business_spans = settings.get("topology_include_business_spans", True)
    if not isinstance(topology_include_business_spans, bool):
        raise ValueError(
            "trace_otel_otlp.settings.topology_include_business_spans must be a boolean when provided"
        )
    trace_view = settings.get("trace_view", "topology")
    if not isinstance(trace_view, str) or trace_view not in {"logical", "topology"}:
        raise ValueError("trace_otel_otlp.settings.trace_view must be one of: ['logical', 'topology']")
    isolate_view_ids = settings.get("isolate_view_ids", False)
    if not isinstance(isolate_view_ids, bool):
        raise ValueError("trace_otel_otlp.settings.isolate_view_ids must be a boolean when provided")
    include_runtime_resource = settings.get("include_runtime_resource", True)
    if not isinstance(include_runtime_resource, bool):
        raise ValueError("trace_otel_otlp.settings.include_runtime_resource must be a boolean when provided")
    span_kind = settings.get("span_kind", "SPAN_KIND_INTERNAL")
    if not isinstance(span_kind, str) or not span_kind:
        raise ValueError("trace_otel_otlp.settings.span_kind must be a non-empty string when provided")
    batch = settings.get("batch", {})
    if not isinstance(batch, dict):
        raise ValueError("trace_otel_otlp.settings.batch must be a mapping when provided")
    batch_max_items = batch.get("max_items", 1)
    if not isinstance(batch_max_items, int) or batch_max_items <= 0:
        raise ValueError("trace_otel_otlp.settings.batch.max_items must be an integer > 0 when provided")
    batch_flush_interval_ms = batch.get("flush_interval_ms", 0)
    if not isinstance(batch_flush_interval_ms, int) or batch_flush_interval_ms < 0:
        raise ValueError(
            "trace_otel_otlp.settings.batch.flush_interval_ms must be an integer >= 0 when provided"
        )
    queue = settings.get("queue", {})
    if not isinstance(queue, dict):
        raise ValueError("trace_otel_otlp.settings.queue must be a mapping when provided")
    queue_max_items = queue.get("max_items", 10000)
    if not isinstance(queue_max_items, int) or queue_max_items <= 0:
        raise ValueError("trace_otel_otlp.settings.queue.max_items must be an integer > 0 when provided")
    queue_drop_policy = queue.get("drop_policy", "drop_newest")
    if not isinstance(queue_drop_policy, str) or queue_drop_policy not in {
        "drop_newest",
        "drop_oldest",
        "block_with_timeout",
    }:
        raise ValueError(
            "trace_otel_otlp.settings.queue.drop_policy must be one of: "
            "['drop_newest', 'drop_oldest', 'block_with_timeout']"
        )
    queue_block_timeout_ms = queue.get("block_timeout_ms", 100)
    if not isinstance(queue_block_timeout_ms, int) or queue_block_timeout_ms <= 0:
        raise ValueError("trace_otel_otlp.settings.queue.block_timeout_ms must be an integer > 0 when provided")
    retry = settings.get("retry", {})
    if not isinstance(retry, dict):
        raise ValueError("trace_otel_otlp.settings.retry must be a mapping when provided")
    retry_max_attempts = retry.get("max_attempts", 0)
    if not isinstance(retry_max_attempts, int) or retry_max_attempts < 0:
        raise ValueError("trace_otel_otlp.settings.retry.max_attempts must be an integer >= 0 when provided")
    retry_backoff_ms = retry.get("backoff_ms", 0)
    if not isinstance(retry_backoff_ms, int) or retry_backoff_ms < 0:
        raise ValueError("trace_otel_otlp.settings.retry.backoff_ms must be an integer >= 0 when provided")
    httpx = settings.get("httpx", {})
    if not isinstance(httpx, dict):
        raise ValueError("trace_otel_otlp.settings.httpx must be a mapping when provided")
    httpx_mode = httpx.get("mode", "sync")
    if not isinstance(httpx_mode, str) or httpx_mode not in {"sync", "async"}:
        raise ValueError("trace_otel_otlp.settings.httpx.mode must be one of: ['sync', 'async']")
    httpx_http2 = httpx.get("http2", False)
    if not isinstance(httpx_http2, bool):
        raise ValueError("trace_otel_otlp.settings.httpx.http2 must be a boolean when provided")
    httpx_max_connections = httpx.get("max_connections")
    if httpx_max_connections is not None and (
        not isinstance(httpx_max_connections, int) or httpx_max_connections <= 0
    ):
        raise ValueError("trace_otel_otlp.settings.httpx.max_connections must be an integer > 0 when provided")
    httpx_max_keepalive_connections = httpx.get("max_keepalive_connections")
    if httpx_max_keepalive_connections is not None and (
        not isinstance(httpx_max_keepalive_connections, int) or httpx_max_keepalive_connections <= 0
    ):
        raise ValueError(
            "trace_otel_otlp.settings.httpx.max_keepalive_connections must be an integer > 0 when provided"
        )
    grpc = settings.get("grpc", {})
    if not isinstance(grpc, dict):
        raise ValueError("trace_otel_otlp.settings.grpc must be a mapping when provided")
    grpc_insecure = grpc.get("insecure", True)
    if not isinstance(grpc_insecure, bool):
        raise ValueError("trace_otel_otlp.settings.grpc.insecure must be a boolean when provided")
    grpc_timeout_seconds = grpc.get("timeout_seconds")
    if grpc_timeout_seconds is not None and (
        not isinstance(grpc_timeout_seconds, (int, float)) or float(grpc_timeout_seconds) <= 0
    ):
        raise ValueError("trace_otel_otlp.settings.grpc.timeout_seconds must be numeric > 0 when provided")
    grpc_retryable_status_codes = grpc.get("retryable_status_codes", ["UNAVAILABLE", "DEADLINE_EXCEEDED"])
    if not isinstance(grpc_retryable_status_codes, list) or not all(
        isinstance(item, str) and item for item in grpc_retryable_status_codes
    ):
        raise ValueError(
            "trace_otel_otlp.settings.grpc.retryable_status_codes must be a list of non-empty strings"
        )
    urllib3 = settings.get("urllib3", {})
    if not isinstance(urllib3, dict):
        raise ValueError("trace_otel_otlp.settings.urllib3 must be a mapping when provided")
    urllib3_num_pools = urllib3.get("num_pools")
    if urllib3_num_pools is not None and (not isinstance(urllib3_num_pools, int) or urllib3_num_pools <= 0):
        raise ValueError("trace_otel_otlp.settings.urllib3.num_pools must be an integer > 0 when provided")
    urllib3_maxsize = urllib3.get("maxsize")
    if urllib3_maxsize is not None and (not isinstance(urllib3_maxsize, int) or urllib3_maxsize <= 0):
        raise ValueError("trace_otel_otlp.settings.urllib3.maxsize must be an integer > 0 when provided")
    urllib3_block = urllib3.get("block")
    if urllib3_block is not None and not isinstance(urllib3_block, bool):
        raise ValueError("trace_otel_otlp.settings.urllib3.block must be a boolean when provided")
    urllib3_timeout_seconds = urllib3.get("timeout_seconds")
    if urllib3_timeout_seconds is not None and (
        not isinstance(urllib3_timeout_seconds, (int, float)) or float(urllib3_timeout_seconds) <= 0
    ):
        raise ValueError(
            "trace_otel_otlp.settings.urllib3.timeout_seconds must be numeric > 0 when provided"
        )
    aiohttp = settings.get("aiohttp", {})
    if not isinstance(aiohttp, dict):
        raise ValueError("trace_otel_otlp.settings.aiohttp must be a mapping when provided")
    aiohttp_shutdown_timeout_seconds = aiohttp.get("shutdown_timeout_seconds", 2.0)
    if not isinstance(aiohttp_shutdown_timeout_seconds, (int, float)) or float(aiohttp_shutdown_timeout_seconds) <= 0:
        raise ValueError(
            "trace_otel_otlp.settings.aiohttp.shutdown_timeout_seconds must be numeric > 0 when provided"
        )
    aiohttp_connector_limit = aiohttp.get("connector_limit")
    if aiohttp_connector_limit is not None and (
        not isinstance(aiohttp_connector_limit, int) or aiohttp_connector_limit <= 0
    ):
        raise ValueError("trace_otel_otlp.settings.aiohttp.connector_limit must be an integer > 0 when provided")
    aiohttp_connector_limit_per_host = aiohttp.get("connector_limit_per_host")
    if aiohttp_connector_limit_per_host is not None and (
        not isinstance(aiohttp_connector_limit_per_host, int) or aiohttp_connector_limit_per_host <= 0
    ):
        raise ValueError(
            "trace_otel_otlp.settings.aiohttp.connector_limit_per_host must be an integer > 0 when provided"
        )
    return OTelOtlpTraceSink(
        endpoint=endpoint,
        backend=backend,
        headers=headers,
        service_name=service_name,
        service_namespace=service_namespace if isinstance(service_namespace, str) else None,
        service_version=service_version if isinstance(service_version, str) else None,
        service_instance_id=service_instance_id if isinstance(service_instance_id, str) else None,
        deployment_environment=deployment_environment if isinstance(deployment_environment, str) else None,
        service_name_by_process_group=service_name_by_process_group,
        service_name_by_step=service_name_by_step,
        service_name_suffix=service_name_suffix if isinstance(service_name_suffix, str) else None,
        logical_include_platform_spans=logical_include_platform_spans,
        topology_include_business_spans=topology_include_business_spans,
        include_runtime_resource=include_runtime_resource,
        trace_view=trace_view,
        isolate_view_ids=isolate_view_ids,
        span_kind=span_kind,
        timeout_seconds=float(timeout_seconds),
        batch_max_items=batch_max_items,
        batch_flush_interval_ms=batch_flush_interval_ms,
        queue_max_items=queue_max_items,
        queue_drop_policy=queue_drop_policy,
        queue_block_timeout_ms=queue_block_timeout_ms,
        retry_max_attempts=retry_max_attempts,
        retry_backoff_ms=retry_backoff_ms,
        httpx_mode=httpx_mode,
        httpx_http2=httpx_http2,
        httpx_max_connections=httpx_max_connections if isinstance(httpx_max_connections, int) else None,
        httpx_max_keepalive_connections=(
            httpx_max_keepalive_connections if isinstance(httpx_max_keepalive_connections, int) else None
        ),
        grpc_insecure=grpc_insecure,
        grpc_timeout_seconds=float(grpc_timeout_seconds) if isinstance(grpc_timeout_seconds, (int, float)) else None,
        grpc_retryable_status_codes=tuple(grpc_retryable_status_codes),
        urllib3_num_pools=urllib3_num_pools if isinstance(urllib3_num_pools, int) else None,
        urllib3_maxsize=urllib3_maxsize if isinstance(urllib3_maxsize, int) else None,
        urllib3_block=urllib3_block if isinstance(urllib3_block, bool) else None,
        urllib3_timeout_seconds=(
            float(urllib3_timeout_seconds) if isinstance(urllib3_timeout_seconds, (int, float)) else None
        ),
        aiohttp_shutdown_timeout_seconds=float(aiohttp_shutdown_timeout_seconds),
        aiohttp_connector_limit=aiohttp_connector_limit if isinstance(aiohttp_connector_limit, int) else None,
        aiohttp_connector_limit_per_host=(
            aiohttp_connector_limit_per_host if isinstance(aiohttp_connector_limit_per_host, int) else None
        ),
        export_fn=export_fn if callable(export_fn) else None,
    )


@adapter(
    name="trace_opentracing_bridge",
    consumes=[TraceMessage],
    emits=[],
    binds=[("kv_stream", TraceMessage)],
    execution_mode="async",
)
def trace_opentracing_bridge(settings: dict[str, object]) -> OpenTracingBridgeTraceSink:
    # Framework-owned OpenTracing compatibility bridge sink.
    bridge_name = settings.get("bridge_name", "opentracing")
    if not isinstance(bridge_name, str) or not bridge_name:
        raise ValueError("trace_opentracing_bridge.settings.bridge_name must be a non-empty string")
    emit_fn = settings.get("_emit_fn")
    if emit_fn is not None and not callable(emit_fn):
        raise ValueError("trace_opentracing_bridge.settings._emit_fn must be callable when provided")
    return OpenTracingBridgeTraceSink(
        bridge_name=bridge_name,
        emit_fn=emit_fn if callable(emit_fn) else None,
    )
