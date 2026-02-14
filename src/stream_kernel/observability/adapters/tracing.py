from __future__ import annotations

from pathlib import Path

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.trace_sinks import (
    JsonlTraceSink,
    OpenTracingBridgeTraceSink,
    OTelOtlpTraceSink,
    StdoutTraceSink,
)
from stream_kernel.observability.domain.tracing import TraceMessage


@adapter(name="trace_stdout", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
def trace_stdout(settings: dict[str, object]) -> StdoutTraceSink:
    # Framework-owned stdout trace sink adapter.
    _ = settings
    return StdoutTraceSink()


@adapter(name="trace_jsonl", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
def trace_jsonl(settings: dict[str, object]) -> JsonlTraceSink:
    # Framework-owned JSONL trace sink adapter.
    path = settings.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("trace_jsonl.settings.path must be a non-empty string")
    return JsonlTraceSink(
        path=Path(path),
        write_mode=str(settings.get("write_mode", "line")),
        flush_every_n=int(settings.get("flush_every_n", 1)),
        flush_every_ms=settings.get("flush_every_ms") if isinstance(settings.get("flush_every_ms"), int) else None,
        fsync_every_n=settings.get("fsync_every_n") if isinstance(settings.get("fsync_every_n"), int) else None,
    )


@adapter(name="trace_otel_otlp", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
def trace_otel_otlp(settings: dict[str, object]) -> OTelOtlpTraceSink:
    # Framework-owned OpenTelemetry OTLP trace exporter sink.
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
    include_runtime_resource = settings.get("include_runtime_resource", True)
    if not isinstance(include_runtime_resource, bool):
        raise ValueError("trace_otel_otlp.settings.include_runtime_resource must be a boolean when provided")
    span_kind = settings.get("span_kind", "SPAN_KIND_INTERNAL")
    if not isinstance(span_kind, str) or not span_kind:
        raise ValueError("trace_otel_otlp.settings.span_kind must be a non-empty string when provided")
    return OTelOtlpTraceSink(
        endpoint=endpoint,
        headers=headers,
        service_name=service_name,
        service_namespace=service_namespace if isinstance(service_namespace, str) else None,
        service_version=service_version if isinstance(service_version, str) else None,
        service_instance_id=service_instance_id if isinstance(service_instance_id, str) else None,
        deployment_environment=deployment_environment if isinstance(deployment_environment, str) else None,
        service_name_by_process_group=service_name_by_process_group,
        include_runtime_resource=include_runtime_resource,
        span_kind=span_kind,
        timeout_seconds=float(timeout_seconds),
        export_fn=export_fn if callable(export_fn) else None,
    )


@adapter(name="trace_opentracing_bridge", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
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
