from __future__ import annotations

import json
import os
import platform
import socket
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib import request as urllib_request

if TYPE_CHECKING:
    from stream_kernel.kernel.trace import TraceRecord


class JsonlTraceSink:
    # JsonlTraceSink writes one TraceRecord per line (trace runtime docs).
    def __init__(
        self,
        *,
        path: Path,
        write_mode: Literal["line", "batch"] = "line",
        flush_every_n: int = 1,
        flush_every_ms: int | None = None,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = path
        self._write_mode = write_mode
        self._flush_every_n = max(1, flush_every_n)
        self._flush_every_ms = flush_every_ms  # reserved, not used in sync runtime
        self._fsync_every_n = fsync_every_n
        self._emit_count = 0
        self._buffer: list[str] = []
        self._handle = self._path.open("a", encoding="utf-8")

    def emit(self, record: "TraceRecord") -> None:
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        if self._write_mode == "batch":
            self._buffer.append(line)
            if len(self._buffer) >= self._flush_every_n:
                self._write_lines(self._buffer)
                self._buffer.clear()
        else:
            self._write_lines([line])
            if self._emit_count % self._flush_every_n == 0:
                self.flush()
        self._emit_count += 1
        if self._fsync_every_n and self._emit_count % self._fsync_every_n == 0:
            os.fsync(self._handle.fileno())

    def flush(self) -> None:
        if self._buffer:
            self._write_lines(self._buffer)
            self._buffer.clear()
        self._handle.flush()

    def close(self) -> None:
        self.flush()
        self._handle.close()

    def _write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._handle.write(line + "\n")


class StdoutTraceSink:
    # StdoutTraceSink prints one JSON record per line for local debugging.
    def emit(self, record: "TraceRecord") -> None:
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        sys.stdout.write(line + "\n")

    def flush(self) -> None:
        sys.stdout.flush()

    def close(self) -> None:
        self.flush()


class OTelOtlpTraceSink:
    # OTel OTLP-compatible trace sink adapter with isolated exporter failures.
    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str] | None = None,
        service_name: str = "stream-kernel",
        service_namespace: str | None = None,
        service_version: str | None = None,
        service_instance_id: str | None = None,
        deployment_environment: str | None = None,
        service_name_by_process_group: bool = True,
        include_runtime_resource: bool = True,
        span_kind: str = "SPAN_KIND_INTERNAL",
        export_fn: Callable[[dict[str, object]], None] | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._headers = dict(headers or {})
        self._service_name = service_name
        self._service_namespace = service_namespace
        self._service_version = service_version
        self._service_instance_id = service_instance_id
        self._deployment_environment = deployment_environment
        self._service_name_by_process_group = service_name_by_process_group
        self._include_runtime_resource = include_runtime_resource
        self._span_kind = span_kind
        self._export_fn = export_fn
        self._timeout_seconds = float(timeout_seconds)
        self._exported = 0
        self._dropped = 0

    def emit(self, record: "TraceRecord") -> None:
        span = _trace_to_otel_span(
            record,
            endpoint=self._endpoint,
            headers=self._headers,
            service_name=self._service_name,
            service_name_by_process_group=self._service_name_by_process_group,
            span_kind=self._span_kind,
        )
        try:
            if callable(self._export_fn):
                self._export_fn(span)
            else:
                self._post_http(span)
        except Exception:
            self._dropped += 1
            return
        self._exported += 1

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.flush()

    def diagnostics(self) -> dict[str, int]:
        return {"exported": self._exported, "dropped": self._dropped}

    def _post_http(self, span: dict[str, object]) -> None:
        payload = _span_to_otlp_http_payload(
            span,
            service_name=self._service_name,
            service_namespace=self._service_namespace,
            service_version=self._service_version,
            service_instance_id=self._service_instance_id,
            deployment_environment=self._deployment_environment,
            include_runtime_resource=self._include_runtime_resource,
        )
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        ).encode("utf-8")
        request = urllib_request.Request(
            self._endpoint,
            data=body,
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        for key, value in self._headers.items():
            request.add_header(key, value)
        with urllib_request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310 - configured endpoint
            status = getattr(response, "status", None)
            if not isinstance(status, int):
                getcode = getattr(response, "getcode", None)
                status = getcode() if callable(getcode) else 200
            if status >= 400:
                raise OSError(f"otlp_http_export_failed:{status}")


class OpenTracingBridgeTraceSink:
    # OpenTracing bridge sink for legacy tracer compatibility mode.
    def __init__(
        self,
        *,
        bridge_name: str = "opentracing",
        emit_fn: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._bridge_name = bridge_name
        self._emit_fn = emit_fn
        self._exported = 0
        self._dropped = 0

    def emit(self, record: "TraceRecord") -> None:
        span = _trace_to_opentracing_span(record, bridge_name=self._bridge_name)
        if callable(self._emit_fn):
            try:
                self._emit_fn(span)
            except Exception:
                self._dropped += 1
                return
        self._exported += 1

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.flush()

    def diagnostics(self) -> dict[str, int]:
        return {"exported": self._exported, "dropped": self._dropped}


def _trace_to_dict(record: "TraceRecord") -> dict[str, object]:
    # Keep key order stable so diffs stay deterministic in tests and diagnostics.
    return {
        "trace_id": record.trace_id,
        "scenario": record.scenario,
        "step_index": record.step_index,
        "step_name": record.step_name,
        "work_index": record.work_index,
        "t_enter": _format_dt(record.t_enter),
        "t_exit": _format_dt(record.t_exit),
        "duration_ms": record.duration_ms,
        "msg_in": _as_dict(record.msg_in),
        "msg_out": [_as_dict(item) for item in record.msg_out],
        "msg_out_count": record.msg_out_count,
        "ctx_before": record.ctx_before,
        "ctx_after": record.ctx_after,
        "ctx_diff": record.ctx_diff,
        "status": record.status,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "error": _as_dict(record.error) if record.error is not None else None,
        "route": _as_dict(record.route) if record.route is not None else None,
    }


def _trace_to_otel_span(
    record: "TraceRecord",
    *,
    endpoint: str,
    headers: dict[str, str],
    service_name: str,
    service_name_by_process_group: bool,
    span_kind: str,
) -> dict[str, object]:
    process_group = record.route.process_group if record.route is not None else None
    resolved_service_name = (
        f"{service_name}.{process_group}"
        if service_name_by_process_group and isinstance(process_group, str) and process_group
        else service_name
    )
    return {
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "name": record.step_name,
        "kind": span_kind,
        "start_time_unix_nano": int(record.t_enter.timestamp() * 1_000_000_000),
        "end_time_unix_nano": int(record.t_exit.timestamp() * 1_000_000_000),
        "status": record.status,
        "resource": {"service.name": resolved_service_name},
        "attributes": {
            "scenario": record.scenario,
            "step_index": record.step_index,
            "work_index": record.work_index,
            "msg_out_count": record.msg_out_count,
            "process_group": process_group,
            "handoff_from": record.route.handoff_from if record.route is not None else None,
            "route_hop": record.route.route_hop if record.route is not None else None,
            "stream_kernel.span_id": record.span_id,
            "stream_kernel.parent_span_id": record.parent_span_id,
        },
        "transport": {
            "endpoint": endpoint,
            "headers": dict(headers),
        },
    }


def _span_to_otlp_http_payload(
    span: dict[str, object],
    *,
    service_name: str,
    service_namespace: str | None = None,
    service_version: str | None = None,
    service_instance_id: str | None = None,
    deployment_environment: str | None = None,
    include_runtime_resource: bool = True,
) -> dict[str, object]:
    trace_id_text = str(span.get("trace_id", ""))
    span_id_text = span.get("span_id")
    parent_span_id_text = span.get("parent_span_id")
    span_name = str(span.get("name", "stream_kernel.step"))
    start_ns = int(span.get("start_time_unix_nano", 0))
    end_ns = int(span.get("end_time_unix_nano", start_ns))
    status = str(span.get("status", "ok")).lower()
    span_kind = str(span.get("kind", "SPAN_KIND_INTERNAL"))
    raw_attrs = span.get("attributes", {})
    attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
    raw_resource = span.get("resource", {})
    resource = raw_resource if isinstance(raw_resource, dict) else {}

    trace_hex = _stable_hex_id(trace_id_text, size_bytes=16)
    span_hex = _normalize_span_id(span_id_text) or _stable_hex_id(
        f"{trace_id_text}:{span_name}:{start_ns}:{end_ns}",
        size_bytes=8,
    )
    parent_hex = _normalize_span_id(parent_span_id_text)

    span_attrs = [
        _otlp_attr("stream_kernel.trace_id", trace_id_text),
        *[_otlp_attr(key, value) for key, value in attrs.items() if value is not None],
    ]
    status_code = "STATUS_CODE_OK" if status == "ok" else "STATUS_CODE_ERROR" if status == "error" else "STATUS_CODE_UNSET"

    resource_attributes = [_otlp_attr("service.name", str(resource.get("service.name", service_name)))]
    if isinstance(service_namespace, str) and service_namespace:
        resource_attributes.append(_otlp_attr("service.namespace", service_namespace))
    if isinstance(service_version, str) and service_version:
        resource_attributes.append(_otlp_attr("service.version", service_version))
    if isinstance(service_instance_id, str) and service_instance_id:
        resource_attributes.append(_otlp_attr("service.instance.id", service_instance_id))
    if isinstance(deployment_environment, str) and deployment_environment:
        resource_attributes.append(_otlp_attr("deployment.environment.name", deployment_environment))
    if include_runtime_resource:
        resource_attributes.extend(_runtime_resource_attributes())

    otlp_span: dict[str, object] = {
        "traceId": trace_hex,
        "spanId": span_hex,
        "kind": span_kind,
        "name": span_name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": span_attrs,
        "status": {"code": status_code},
    }
    if isinstance(parent_hex, str):
        otlp_span["parentSpanId"] = parent_hex

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": resource_attributes
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "stream-kernel"},
                        "spans": [otlp_span],
                    }
                ],
            }
        ]
    }


def _stable_hex_id(value: str, *, size_bytes: int) -> str:
    digest = sha256(value.encode("utf-8")).digest()
    return digest[:size_bytes].hex()


def _normalize_span_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if len(candidate) != 16:
        return None
    if any(ch not in "0123456789abcdef" for ch in candidate):
        return None
    return candidate


def _runtime_resource_attributes() -> list[dict[str, object]]:
    return [
        _otlp_attr("host.name", socket.gethostname()),
        _otlp_attr("process.pid", os.getpid()),
        _otlp_attr("process.runtime.name", platform.python_implementation()),
        _otlp_attr("process.runtime.version", platform.python_version()),
        _otlp_attr("telemetry.sdk.name", "stream-kernel"),
        _otlp_attr("telemetry.sdk.language", "python"),
    ]


def _otlp_attr(key: str, value: object) -> dict[str, object]:
    return {
        "key": key,
        "value": _otlp_any_value(value),
    }


def _otlp_any_value(value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, (dict, list, tuple)):
        return {"stringValue": json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=_json_default)}
    return {"stringValue": str(value)}


def _trace_to_opentracing_span(record: "TraceRecord", *, bridge_name: str) -> dict[str, object]:
    logs: list[dict[str, object]] = []
    if record.error is not None:
        logs.append(
            {
                "event": "error",
                "error.type": record.error.type,
                "error.message": record.error.message,
                "error.where": record.error.where,
            }
        )
    return {
        "bridge": bridge_name,
        "trace_id": record.trace_id,
        "operation_name": record.step_name,
        "start_time_ms": int(record.t_enter.timestamp() * 1000),
        "finish_time_ms": int(record.t_exit.timestamp() * 1000),
        "tags": {
            "scenario": record.scenario,
            "step_index": record.step_index,
            "status": record.status,
            "span_id": record.span_id,
            "parent_span_id": record.parent_span_id,
            "process_group": record.route.process_group if record.route is not None else None,
            "handoff_from": record.route.handoff_from if record.route is not None else None,
            "route_hop": record.route.route_hop if record.route is not None else None,
        },
        "logs": logs,
    }


def _as_dict(obj: object) -> object:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _format_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(obj: object) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)
