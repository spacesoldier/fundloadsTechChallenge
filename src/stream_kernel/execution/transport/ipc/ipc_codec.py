from __future__ import annotations

import base64
import json
import pickle
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from stream_kernel.kernel.trace import ErrorInfo, MessageSignature, RouteInfo, TraceRecord
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.monitoring import MonitoringMessage
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
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcControlSignal
from stream_kernel.routing.envelope import Envelope

_TAG_KEY = "__sk_type__"
_DATA_KEY = "__sk_data__"
_SUPPORTED_CODECS = {"bytes", "pickle"}


class ExecutionIpcCodecError(ValueError):
    # Raised when bytes codec cannot encode/decode a payload deterministically.
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIpcCodec:
    mode: str

    def __post_init__(self) -> None:
        if self.mode not in _SUPPORTED_CODECS:
            raise ExecutionIpcCodecError(f"unsupported ipc codec: {self.mode}")

    def encode(self, payload: object) -> bytes:
        if self.mode == "pickle":
            return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        return _encode_json_bytes(payload)

    def decode(self, payload: bytes) -> object:
        if self.mode == "pickle":
            return pickle.loads(payload)
        return _decode_json_bytes(payload)


def _encode_json_bytes(payload: object) -> bytes:
    wire = _to_wire(payload)
    return json.dumps(wire, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json_bytes(payload: bytes) -> object:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ExecutionIpcCodecError("bytes codec expects a bytes payload")
    try:
        raw = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionIpcCodecError("bytes codec payload must be valid utf-8 json") from exc
    return _from_wire(raw)


def _wrap(tag: str, data: object) -> dict[str, object]:
    return {_TAG_KEY: tag, _DATA_KEY: data}


def _to_wire(payload: object) -> object:
    if payload is None or isinstance(payload, (bool, int, float, str)):
        return payload
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return _wrap("bytes", base64.b64encode(bytes(payload)).decode("ascii"))
    if isinstance(payload, datetime):
        return _wrap("datetime", _format_dt(payload))
    if isinstance(payload, date):
        return _wrap("date", payload.isoformat())
    if isinstance(payload, Decimal):
        return _wrap("decimal", str(payload))
    if isinstance(payload, list):
        return [_to_wire(item) for item in payload]
    if isinstance(payload, tuple):
        return _wrap("tuple", [_to_wire(item) for item in payload])
    if isinstance(payload, dict):
        encoded: dict[str, object] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                raise ExecutionIpcCodecError("bytes codec requires dict keys to be strings")
            encoded[key] = _to_wire(value)
        return encoded
    if isinstance(payload, Envelope):
        return _wrap(
            "Envelope",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "target": _to_wire(payload.target),
                "topic": payload.topic,
                "reply_to": payload.reply_to,
                "span_id": payload.span_id,
            },
        )
    if isinstance(payload, TraceRecord):
        return _wrap("TraceRecord", _trace_record_to_wire(payload))
    if isinstance(payload, MessageSignature):
        return _wrap(
            "MessageSignature",
            {
                "type_name": payload.type_name,
                "identity": payload.identity,
                "hash": payload.hash,
            },
        )
    if isinstance(payload, ErrorInfo):
        return _wrap(
            "ErrorInfo",
            {
                "type": payload.type,
                "message": payload.message,
                "where": payload.where,
                "stack": payload.stack,
            },
        )
    if isinstance(payload, RouteInfo):
        return _wrap(
            "RouteInfo",
            {
                "process_group": payload.process_group,
                "handoff_from": payload.handoff_from,
                "route_hop": payload.route_hop,
                "parent_span_id": payload.parent_span_id,
                "runner_gap_ms": payload.runner_gap_ms,
            },
        )
    if isinstance(payload, LogMessage):
        return _wrap(
            "LogMessage",
            {
                "level": payload.level,
                "message": payload.message,
                "timestamp": _format_dt(payload.timestamp),
                "fields": _to_wire(payload.fields),
            },
        )
    if isinstance(payload, DebugMessage):
        return _wrap(
            "DebugMessage",
            {
                "timestamp": _format_dt(payload.timestamp),
                "event": payload.event,
                "source": payload.source,
                "fields": _to_wire(payload.fields),
                "run_id": payload.run_id,
                "run_instance_id": payload.run_instance_id,
                "process_group": payload.process_group,
                "worker_id": payload.worker_id,
                "trace_id": payload.trace_id,
            },
        )
    if isinstance(payload, MonitoringMessage):
        return _wrap(
            "MonitoringMessage",
            {
                "name": payload.name,
                "status": payload.status,
                "timestamp": _format_dt(payload.timestamp),
                "details": _to_wire(payload.details),
            },
        )
    if isinstance(payload, MonitoringMetricsSnapshotEvent):
        return _wrap(
            "MonitoringMetricsSnapshotEvent",
            {"stage": payload.stage, "snapshot": _to_wire(payload.snapshot)},
        )
    if isinstance(payload, MonitoringMetricsSnapshotResult):
        return _wrap(
            "MonitoringMetricsSnapshotResult",
            {
                "stage": payload.stage,
                "snapshot": _to_wire(payload.snapshot),
                "metric_records": _to_wire(payload.metric_records),
            },
        )
    if isinstance(payload, WorkerQueueTelemetryEvent):
        return _wrap(
            "WorkerQueueTelemetryEvent",
            {
                "group_name": payload.group_name,
                "worker_id": payload.worker_id,
                "pid": payload.pid,
                "queue_depth": payload.queue_depth,
                "inflight": payload.inflight,
                "runner_profile": payload.runner_profile,
                "ts_epoch_ms": payload.ts_epoch_ms,
            },
        )
    if isinstance(payload, TraceDispatchEvent):
        return _wrap(
            "TraceDispatchEvent",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "attributes": _to_wire(payload.attributes),
            },
        )
    if isinstance(payload, LogDispatchEvent):
        return _wrap(
            "LogDispatchEvent",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "attributes": _to_wire(payload.attributes),
            },
        )
    if isinstance(payload, DebugDispatchEvent):
        return _wrap(
            "DebugDispatchEvent",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "attributes": _to_wire(payload.attributes),
            },
        )
    if isinstance(payload, MetricDispatchEvent):
        return _wrap(
            "MetricDispatchEvent",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "attributes": _to_wire(payload.attributes),
            },
        )
    if isinstance(payload, MonitorDispatchEvent):
        return _wrap(
            "MonitorDispatchEvent",
            {
                "payload": _to_wire(payload.payload),
                "trace_id": payload.trace_id,
                "attributes": _to_wire(payload.attributes),
            },
        )
    if isinstance(payload, ExecutionIpcControlSignal):
        return _wrap(
            "ExecutionIpcControlSignal",
            {
                "kind": payload.kind,
                "count": payload.count,
            },
        )
    discovery_record_type = _discovery_entity_record_type()
    if discovery_record_type is not None and isinstance(payload, discovery_record_type):
        return _wrap(
            "ControlPlaneDiscoveryEntityRecord",
            {
                "entity_kind": payload.entity_kind,
                "entity_id": payload.entity_id,
                "source_scope": payload.source_scope,
                "module": payload.module,
                "qualname": payload.qualname,
                "meta": _to_wire(payload.meta),
            },
        )
    request_type, snapshot_type, ack_type = _leaf_discovery_event_types()
    if request_type is not None and isinstance(payload, request_type):
        return _wrap(
            "ControlPlaneLeafDiscoveryRequestEvent",
            {
                "target_group": payload.target_group,
                "worker_id": payload.worker_id,
                "request_id": payload.request_id,
                "required_nodes": _to_wire(list(payload.required_nodes)),
                "include_relationships": payload.include_relationships,
                "protocol_revision": payload.protocol_revision,
                "issued_at_epoch_ms": payload.issued_at_epoch_ms,
            },
        )
    if snapshot_type is not None and isinstance(payload, snapshot_type):
        return _wrap(
            "ControlPlaneLeafDiscoverySnapshotEvent",
            {
                "target_group": payload.target_group,
                "worker_id": payload.worker_id,
                "request_id": payload.request_id,
                "required_nodes": _to_wire(list(payload.required_nodes)),
                "snapshot_records": _to_wire(list(payload.snapshot_records)),
                "protocol_revision": payload.protocol_revision,
                "issued_at_epoch_ms": payload.issued_at_epoch_ms,
            },
        )
    if ack_type is not None and isinstance(payload, ack_type):
        return _wrap(
            "ControlPlaneLeafDiscoveryAckEvent",
            {
                "target_group": payload.target_group,
                "worker_id": payload.worker_id,
                "request_id": payload.request_id,
                "status": payload.status,
                "discovered_nodes": _to_wire(list(payload.discovered_nodes)),
                "missing_nodes": _to_wire(list(payload.missing_nodes)),
                "error": payload.error,
                "emitted_at_epoch_ms": payload.emitted_at_epoch_ms,
            },
        )
    raise ExecutionIpcCodecError(
        "bytes codec does not support payload type "
        f"{type(payload).__name__}; use runtime.platform.execution_ipc.codec=pickle"
    )


def _from_wire(payload: object) -> object:
    if payload is None or isinstance(payload, (bool, int, float, str)):
        return payload
    if isinstance(payload, list):
        return [_from_wire(item) for item in payload]
    if isinstance(payload, dict):
        if _TAG_KEY in payload and _DATA_KEY in payload:
            tag = payload.get(_TAG_KEY)
            data = payload.get(_DATA_KEY)
            decoder = _DECODERS.get(tag)
            if decoder is None:
                raise ExecutionIpcCodecError(f"unsupported bytes codec tag: {tag}")
            return decoder(data)
        return {key: _from_wire(value) for key, value in payload.items()}
    return payload


def _decode_bytes(data: object) -> bytes:
    if not isinstance(data, str) or not data:
        raise ExecutionIpcCodecError("bytes codec requires base64 string payload")
    try:
        return base64.b64decode(data.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ExecutionIpcCodecError("bytes codec payload is not valid base64") from exc


def _decode_datetime(data: object) -> datetime:
    if not isinstance(data, str) or not data:
        raise ExecutionIpcCodecError("datetime payload must be a non-empty string")
    return _parse_dt(data)


def _decode_date(data: object) -> date:
    if not isinstance(data, str) or not data:
        raise ExecutionIpcCodecError("date payload must be a non-empty string")
    try:
        return date.fromisoformat(data)
    except ValueError as exc:
        raise ExecutionIpcCodecError("date payload must be isoformat") from exc


def _decode_decimal(data: object) -> Decimal:
    if not isinstance(data, str) or not data:
        raise ExecutionIpcCodecError("decimal payload must be a non-empty string")
    try:
        return Decimal(data)
    except Exception as exc:  # noqa: BLE001 - keep deterministic error path.
        raise ExecutionIpcCodecError("decimal payload must be a valid decimal string") from exc


def _decode_tuple(data: object) -> tuple[object, ...]:
    if not isinstance(data, list):
        raise ExecutionIpcCodecError("tuple payload must be a list")
    return tuple(_from_wire(item) for item in data)


def _decode_envelope(data: object) -> Envelope:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("Envelope payload must be a mapping")
    return Envelope(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        target=_from_wire(data.get("target")),
        topic=data.get("topic"),
        reply_to=data.get("reply_to"),
        span_id=data.get("span_id"),
    )


def _decode_message_signature(data: object) -> MessageSignature:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MessageSignature payload must be a mapping")
    return MessageSignature(
        type_name=data.get("type_name") or "",
        identity=data.get("identity"),
        hash=data.get("hash"),
    )


def _decode_error_info(data: object) -> ErrorInfo:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ErrorInfo payload must be a mapping")
    return ErrorInfo(
        type=data.get("type") or "",
        message=data.get("message") or "",
        where=data.get("where") or "",
        stack=data.get("stack"),
    )


def _decode_route_info(data: object) -> RouteInfo:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("RouteInfo payload must be a mapping")
    return RouteInfo(
        process_group=data.get("process_group"),
        handoff_from=data.get("handoff_from"),
        route_hop=data.get("route_hop"),
        parent_span_id=data.get("parent_span_id"),
        runner_gap_ms=data.get("runner_gap_ms"),
    )


def _decode_trace_record(data: object) -> TraceRecord:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("TraceRecord payload must be a mapping")
    msg_in_raw = _from_wire(data.get("msg_in"))
    if not isinstance(msg_in_raw, MessageSignature):
        raise ExecutionIpcCodecError("TraceRecord.msg_in must be a MessageSignature")
    msg_out_raw = data.get("msg_out", [])
    if not isinstance(msg_out_raw, list):
        raise ExecutionIpcCodecError("TraceRecord.msg_out must be a list")
    msg_out = tuple(
        _from_wire(item)
        for item in msg_out_raw
    )
    if not all(isinstance(item, MessageSignature) for item in msg_out):
        raise ExecutionIpcCodecError("TraceRecord.msg_out entries must be MessageSignature")
    error_raw = data.get("error")
    route_raw = data.get("route")
    error_obj = _from_wire(error_raw) if error_raw is not None else None
    route_obj = _from_wire(route_raw) if route_raw is not None else None
    return TraceRecord(
        trace_id=data.get("trace_id") or "",
        scenario=data.get("scenario") or "",
        step_index=int(data.get("step_index") or 0),
        step_name=data.get("step_name") or "",
        work_index=int(data.get("work_index") or 0),
        t_enter=_parse_dt(data.get("t_enter") or ""),
        t_exit=_parse_dt(data.get("t_exit") or ""),
        duration_ms=float(data.get("duration_ms") or 0.0),
        msg_in=msg_in_raw,
        msg_out=msg_out,
        msg_out_count=int(data.get("msg_out_count") or len(msg_out)),
        ctx_before=_from_wire(data.get("ctx_before")),
        ctx_after=_from_wire(data.get("ctx_after")),
        ctx_diff=_from_wire(data.get("ctx_diff")),
        status=data.get("status") or "ok",
        error=error_obj if isinstance(error_obj, ErrorInfo) else None,
        route=route_obj if isinstance(route_obj, RouteInfo) else None,
        span_id=data.get("span_id"),
        parent_span_id=data.get("parent_span_id"),
    )


def _decode_log_message(data: object) -> LogMessage:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("LogMessage payload must be a mapping")
    return LogMessage(
        level=data.get("level") or "",
        message=data.get("message") or "",
        timestamp=_parse_dt(data.get("timestamp") or ""),
        fields=_from_wire(data.get("fields") or {}),
    )


def _decode_monitoring_message(data: object) -> MonitoringMessage:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MonitoringMessage payload must be a mapping")
    return MonitoringMessage(
        name=data.get("name") or "",
        status=data.get("status") or "",
        timestamp=_parse_dt(data.get("timestamp") or ""),
        details=_from_wire(data.get("details") or {}),
    )


def _decode_debug_message(data: object) -> DebugMessage:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("DebugMessage payload must be a mapping")
    return DebugMessage(
        timestamp=_parse_dt(data.get("timestamp") or ""),
        event=data.get("event") or "",
        source=data.get("source") or "",
        fields=_from_wire(data.get("fields") or {}),
        run_id=data.get("run_id"),
        run_instance_id=data.get("run_instance_id"),
        process_group=data.get("process_group"),
        worker_id=data.get("worker_id"),
        trace_id=data.get("trace_id"),
    )


def _decode_snapshot_event(data: object) -> MonitoringMetricsSnapshotEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MonitoringMetricsSnapshotEvent payload must be a mapping")
    return MonitoringMetricsSnapshotEvent(
        stage=data.get("stage") or "",
        snapshot=_from_wire(data.get("snapshot") or {}),
    )


def _decode_snapshot_result(data: object) -> MonitoringMetricsSnapshotResult:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MonitoringMetricsSnapshotResult payload must be a mapping")
    return MonitoringMetricsSnapshotResult(
        stage=data.get("stage") or "",
        snapshot=_from_wire(data.get("snapshot") or {}),
        metric_records=_from_wire(data.get("metric_records") or []),
    )


def _decode_worker_queue_telemetry(data: object) -> WorkerQueueTelemetryEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("WorkerQueueTelemetryEvent payload must be a mapping")
    return WorkerQueueTelemetryEvent(
        group_name=data.get("group_name") or "",
        worker_id=data.get("worker_id") or "",
        pid=int(data.get("pid") or 0),
        queue_depth=int(data.get("queue_depth") or 0),
        inflight=int(data.get("inflight") or 0),
        runner_profile=data.get("runner_profile") or "",
        ts_epoch_ms=int(data.get("ts_epoch_ms") or 0),
    )


def _decode_trace_dispatch_event(data: object) -> TraceDispatchEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("TraceDispatchEvent payload must be a mapping")
    return TraceDispatchEvent(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        attributes=_from_wire(data.get("attributes") or {}),
    )


def _decode_log_dispatch_event(data: object) -> LogDispatchEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("LogDispatchEvent payload must be a mapping")
    return LogDispatchEvent(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        attributes=_from_wire(data.get("attributes") or {}),
    )


def _decode_debug_dispatch_event(data: object) -> DebugDispatchEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("DebugDispatchEvent payload must be a mapping")
    return DebugDispatchEvent(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        attributes=_from_wire(data.get("attributes") or {}),
    )


def _decode_metric_dispatch_event(data: object) -> MetricDispatchEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MetricDispatchEvent payload must be a mapping")
    return MetricDispatchEvent(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        attributes=_from_wire(data.get("attributes") or {}),
    )


def _decode_monitor_dispatch_event(data: object) -> MonitorDispatchEvent:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("MonitorDispatchEvent payload must be a mapping")
    return MonitorDispatchEvent(
        payload=_from_wire(data.get("payload")),
        trace_id=data.get("trace_id"),
        attributes=_from_wire(data.get("attributes") or {}),
    )


def _decode_execution_ipc_control_signal(data: object) -> ExecutionIpcControlSignal:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ExecutionIpcControlSignal payload must be a mapping")
    kind = data.get("kind")
    count = data.get("count", 0)
    if not isinstance(kind, str) or not kind:
        raise ExecutionIpcCodecError("ExecutionIpcControlSignal.kind must be a non-empty string")
    if not isinstance(count, int):
        raise ExecutionIpcCodecError("ExecutionIpcControlSignal.count must be an integer")
    return ExecutionIpcControlSignal(kind=kind, count=count)


def _decode_discovery_entity_record(data: object) -> object:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ControlPlaneDiscoveryEntityRecord payload must be a mapping")
    record_type = _discovery_entity_record_type()
    if record_type is None:
        raise ExecutionIpcCodecError("ControlPlaneDiscoveryEntityRecord type is unavailable")
    meta = _from_wire(data.get("meta") or {})
    if not isinstance(meta, dict):
        raise ExecutionIpcCodecError("ControlPlaneDiscoveryEntityRecord.meta must be a mapping")
    return record_type(
        entity_kind=data.get("entity_kind") or "",
        entity_id=data.get("entity_id") or "",
        source_scope=data.get("source_scope") or "",
        module=data.get("module") or "",
        qualname=data.get("qualname") or "",
        meta=meta,
    )


def _decode_leaf_discovery_request_event(data: object) -> object:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoveryRequestEvent payload must be a mapping")
    request_type, _snapshot_type, _ack_type = _leaf_discovery_event_types()
    if request_type is None:
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoveryRequestEvent type is unavailable")
    required_nodes = _from_wire(data.get("required_nodes", []))
    if not isinstance(required_nodes, list):
        raise ExecutionIpcCodecError(
            "ControlPlaneLeafDiscoveryRequestEvent.required_nodes must be a list"
        )
    return request_type(
        target_group=data.get("target_group") or "",
        worker_id=data.get("worker_id") or "",
        request_id=data.get("request_id") or "",
        required_nodes=tuple(required_nodes),
        include_relationships=bool(data.get("include_relationships", False)),
        protocol_revision=int(data.get("protocol_revision", 1)),
        issued_at_epoch_ms=int(data.get("issued_at_epoch_ms", 0)),
    )


def _decode_leaf_discovery_snapshot_event(data: object) -> object:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoverySnapshotEvent payload must be a mapping")
    _request_type, snapshot_type, _ack_type = _leaf_discovery_event_types()
    if snapshot_type is None:
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoverySnapshotEvent type is unavailable")
    required_nodes = _from_wire(data.get("required_nodes", []))
    snapshot_records = _from_wire(data.get("snapshot_records", []))
    if not isinstance(required_nodes, list):
        raise ExecutionIpcCodecError(
            "ControlPlaneLeafDiscoverySnapshotEvent.required_nodes must be a list"
        )
    if not isinstance(snapshot_records, list):
        raise ExecutionIpcCodecError(
            "ControlPlaneLeafDiscoverySnapshotEvent.snapshot_records must be a list"
        )
    return snapshot_type(
        target_group=data.get("target_group") or "",
        worker_id=data.get("worker_id") or "",
        request_id=data.get("request_id") or "",
        required_nodes=tuple(required_nodes),
        snapshot_records=tuple(snapshot_records),
        protocol_revision=int(data.get("protocol_revision", 1)),
        issued_at_epoch_ms=int(data.get("issued_at_epoch_ms", 0)),
    )


def _decode_leaf_discovery_ack_event(data: object) -> object:
    if not isinstance(data, dict):
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoveryAckEvent payload must be a mapping")
    _request_type, _snapshot_type, ack_type = _leaf_discovery_event_types()
    if ack_type is None:
        raise ExecutionIpcCodecError("ControlPlaneLeafDiscoveryAckEvent type is unavailable")
    discovered_nodes = _from_wire(data.get("discovered_nodes", []))
    missing_nodes = _from_wire(data.get("missing_nodes", []))
    if not isinstance(discovered_nodes, list):
        raise ExecutionIpcCodecError(
            "ControlPlaneLeafDiscoveryAckEvent.discovered_nodes must be a list"
        )
    if not isinstance(missing_nodes, list):
        raise ExecutionIpcCodecError(
            "ControlPlaneLeafDiscoveryAckEvent.missing_nodes must be a list"
        )
    return ack_type(
        target_group=data.get("target_group") or "",
        worker_id=data.get("worker_id") or "",
        request_id=data.get("request_id") or "",
        status=data.get("status") or "",
        discovered_nodes=tuple(discovered_nodes),
        missing_nodes=tuple(missing_nodes),
        error=data.get("error"),
        emitted_at_epoch_ms=int(data.get("emitted_at_epoch_ms", 0)),
    )


def _trace_record_to_wire(record: TraceRecord) -> dict[str, object]:
    return {
        "trace_id": record.trace_id,
        "scenario": record.scenario,
        "step_index": record.step_index,
        "step_name": record.step_name,
        "work_index": record.work_index,
        "t_enter": _format_dt(record.t_enter),
        "t_exit": _format_dt(record.t_exit),
        "duration_ms": record.duration_ms,
        "msg_in": _to_wire(record.msg_in),
        "msg_out": [_to_wire(item) for item in record.msg_out],
        "msg_out_count": record.msg_out_count,
        "ctx_before": _to_wire(record.ctx_before),
        "ctx_after": _to_wire(record.ctx_after),
        "ctx_diff": _to_wire(record.ctx_diff),
        "status": record.status,
        "error": _to_wire(record.error) if record.error is not None else None,
        "route": _to_wire(record.route) if record.route is not None else None,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
    }


def _format_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    if not value:
        raise ExecutionIpcCodecError("datetime payload must be a non-empty string")
    raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ExecutionIpcCodecError("datetime payload must be isoformat") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _leaf_discovery_event_types() -> tuple[type[object] | None, type[object] | None, type[object] | None]:
    try:
        from stream_kernel.platform.services.runtime.control_plane_events import (
            ControlPlaneLeafDiscoveryAckEvent,
            ControlPlaneLeafDiscoveryRequestEvent,
            ControlPlaneLeafDiscoverySnapshotEvent,
        )
    except Exception:
        return (None, None, None)
    return (
        ControlPlaneLeafDiscoveryRequestEvent,
        ControlPlaneLeafDiscoverySnapshotEvent,
        ControlPlaneLeafDiscoveryAckEvent,
    )


def _discovery_entity_record_type() -> type[object] | None:
    try:
        from stream_kernel.platform.services.runtime.control_plane_events import (
            ControlPlaneDiscoveryEntityRecord,
        )
    except Exception:
        return None
    return ControlPlaneDiscoveryEntityRecord


_DECODERS = {
    "bytes": _decode_bytes,
    "datetime": _decode_datetime,
    "date": _decode_date,
    "decimal": _decode_decimal,
    "tuple": _decode_tuple,
    "Envelope": _decode_envelope,
    "TraceRecord": _decode_trace_record,
    "MessageSignature": _decode_message_signature,
    "ErrorInfo": _decode_error_info,
    "RouteInfo": _decode_route_info,
    "LogMessage": _decode_log_message,
    "DebugMessage": _decode_debug_message,
    "MonitoringMessage": _decode_monitoring_message,
    "MonitoringMetricsSnapshotEvent": _decode_snapshot_event,
    "MonitoringMetricsSnapshotResult": _decode_snapshot_result,
    "WorkerQueueTelemetryEvent": _decode_worker_queue_telemetry,
    "TraceDispatchEvent": _decode_trace_dispatch_event,
    "LogDispatchEvent": _decode_log_dispatch_event,
    "DebugDispatchEvent": _decode_debug_dispatch_event,
    "MetricDispatchEvent": _decode_metric_dispatch_event,
    "MonitorDispatchEvent": _decode_monitor_dispatch_event,
    "ExecutionIpcControlSignal": _decode_execution_ipc_control_signal,
    "ControlPlaneDiscoveryEntityRecord": _decode_discovery_entity_record,
    "ControlPlaneLeafDiscoveryRequestEvent": _decode_leaf_discovery_request_event,
    "ControlPlaneLeafDiscoverySnapshotEvent": _decode_leaf_discovery_snapshot_event,
    "ControlPlaneLeafDiscoveryAckEvent": _decode_leaf_discovery_ack_event,
}
