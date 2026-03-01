from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stream_kernel.kernel.trace import ErrorInfo, MessageSignature, RouteInfo, TraceRecord
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.monitoring import MonitoringMessage
from stream_kernel.observability.events import (
    MonitoringMetricsSnapshotEvent,
    MonitoringMetricsSnapshotResult,
    WorkerQueueTelemetryEvent,
)
from stream_kernel.execution.transport.ipc.ipc_codec import (
    ExecutionIpcCodec,
    ExecutionIpcCodecError,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
)
from stream_kernel.routing.envelope import Envelope


class _PicklePayload:
    def __init__(self) -> None:
        self.value = 42


def _sample_trace_record() -> TraceRecord:
    now = datetime(2026, 2, 22, 12, 0, 0, tzinfo=UTC)
    msg_in = MessageSignature(type_name="Input", identity="id-1", hash=None)
    msg_out = (MessageSignature(type_name="Output", identity=None, hash=None),)
    route = RouteInfo(
        process_group="execution.features",
        handoff_from="execution.ingress",
        route_hop=1,
        parent_span_id="span-1",
        runner_gap_ms=0.5,
    )
    error = ErrorInfo(type="ValueError", message="bad", where="node")
    return TraceRecord(
        trace_id="trace-1",
        scenario="scenario",
        step_index=1,
        step_name="compute",
        work_index=7,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=msg_in,
        msg_out=msg_out,
        msg_out_count=len(msg_out),
        ctx_before={"flag": True},
        ctx_after={"flag": False},
        ctx_diff={"flag": {"before": True, "after": False}},
        status="error",
        error=error,
        route=route,
        span_id="span-2",
        parent_span_id="span-1",
    )


def test_ipc_codec_bytes_roundtrip_trace_record() -> None:
    codec = ExecutionIpcCodec("bytes")
    record = _sample_trace_record()
    message = {"kind": "worker_trace", "record": record}
    payload = codec.encode(message)
    assert isinstance(payload, bytes)
    decoded = codec.decode(payload)
    assert decoded["kind"] == "worker_trace"
    assert decoded["record"] == record


def test_ipc_codec_bytes_roundtrip_observability_payloads() -> None:
    codec = ExecutionIpcCodec("bytes")
    log_message = LogMessage(
        level="info",
        message="hello",
        timestamp=datetime(2026, 2, 22, 12, 1, 0, tzinfo=UTC),
        fields={"run_id": "run-1"},
    )
    monitor_message = MonitoringMessage(
        name="worker_queue_depth",
        status="sample",
        timestamp=datetime(2026, 2, 22, 12, 1, 5, tzinfo=UTC),
        details={"queue_depth": 4},
    )
    snapshot_event = MonitoringMetricsSnapshotEvent(stage="tick", snapshot={"depth": 1})
    snapshot_result = MonitoringMetricsSnapshotResult(
        stage="tick",
        snapshot={"depth": 1},
        metric_records=[{"name": "depth", "value": 1}],
    )
    telemetry = WorkerQueueTelemetryEvent(
        group_name="execution.egress",
        worker_id="execution.egress#1",
        pid=10,
        queue_depth=2,
        inflight=1,
        runner_profile="auto",
        ts_epoch_ms=123456,
    )
    message = {
        "log": log_message,
        "monitor": monitor_message,
        "snapshot": snapshot_event,
        "result": snapshot_result,
        "telemetry": telemetry,
    }
    decoded = codec.decode(codec.encode(message))
    assert decoded["log"] == log_message
    assert decoded["monitor"] == monitor_message
    assert decoded["snapshot"] == snapshot_event
    assert decoded["result"] == snapshot_result
    assert decoded["telemetry"] == telemetry


def test_ipc_codec_bytes_roundtrip_envelope() -> None:
    codec = ExecutionIpcCodec("bytes")
    envelope = Envelope(
        payload={"id": 1, "status": "ok"},
        trace_id="trace-2",
        target="node:alpha",
        topic="topic-1",
        reply_to="reply",
        span_id="span-3",
    )
    decoded = codec.decode(codec.encode(envelope))
    assert decoded == envelope


def test_ipc_codec_bytes_rejects_unknown_payload() -> None:
    class CustomPayload:
        pass

    codec = ExecutionIpcCodec("bytes")
    with pytest.raises(ExecutionIpcCodecError):
        codec.encode({"payload": CustomPayload()})


def test_ipc_codec_pickle_roundtrip_arbitrary_object() -> None:
    payload = _PicklePayload()
    codec = ExecutionIpcCodec("pickle")
    encoded = codec.encode(payload)
    decoded = codec.decode(encoded)
    assert isinstance(decoded, _PicklePayload)
    assert decoded.value == 42


def test_ipc_codec_bytes_roundtrip_leaf_discovery_handshake_events() -> None:
    codec = ExecutionIpcCodec("bytes")
    request = ControlPlaneLeafDiscoveryRequestEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        required_nodes=("compute_features", "idempotency_gate"),
        include_relationships=True,
        protocol_revision=2,
    )
    ack = ControlPlaneLeafDiscoveryAckEvent(
        target_group="execution.features",
        worker_id="execution.features#1",
        request_id="req-1",
        status="accepted",
        discovered_nodes=("compute_features", "idempotency_gate"),
        missing_nodes=(),
    )
    message = {"request": request, "ack": ack}
    decoded = codec.decode(codec.encode(message))
    assert decoded["request"] == request
    assert decoded["ack"] == ack


def test_ipc_codec_bytes_roundtrip_leaf_discovery_snapshot_event() -> None:
    codec = ExecutionIpcCodec("bytes")
    snapshot = ControlPlaneLeafDiscoverySnapshotEvent(
        target_group="execution.egress",
        worker_id="execution.egress#1",
        request_id="req-snapshot-1",
        required_nodes=("format_output", "sink:sink"),
        snapshot_records=(
            ControlPlaneDiscoveryEntityRecord(
                entity_kind="node",
                entity_id="node:format_output",
                source_scope="project",
                module="fund_load.usecases.steps.format_output",
                qualname="format_output",
                meta={"name": "format_output"},
            ),
        ),
        protocol_revision=3,
    )

    decoded = codec.decode(codec.encode({"snapshot": snapshot}))

    assert decoded["snapshot"] == snapshot
