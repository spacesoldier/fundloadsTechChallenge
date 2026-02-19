from __future__ import annotations

from stream_kernel.observability.events import MonitoringMetricsSnapshotEvent
from stream_kernel.platform.services.observability import (
    DefaultObservabilityMetricsDispatchService,
    InMemoryObservabilityMetricsService,
)


def _snapshot_fixture() -> dict[str, object]:
    return {
        "tracing_enabled": True,
        "dispatch_submitted": 11,
        "dispatch_processed": 7,
        "dispatch_failed": 1,
        "dispatch_dropped": 2,
        "dispatch_queue_depth": 3,
        "dispatch_pending": 3,
        "dispatch_submit_block_count": 4,
        "dispatch_submit_timeout_count": 1,
        "dispatch_submit_block_wait_ms_total": 125,
        "dispatch_submit_dropped_total": 5,
        "sink_count": 2,
        "sink_pending_total": 4,
        "pending_total_estimate": 7,
        "sink_diagnostics": [
            {"sink_index": 0, "sink_kind": "JsonlTraceSink", "exported": 9, "dropped": 1, "buffered": 2},
            {"sink_index": 1, "sink_kind": "OTelOtlpTraceSink", "exported": 8, "dropped": 2, "pending": 2},
        ],
    }


def test_observability_metrics_service_aggregates_dispatch_and_sink_metrics() -> None:
    service = InMemoryObservabilityMetricsService()
    service.ingest_trace_dispatch_snapshot(stage="stop_requested", snapshot=_snapshot_fixture())

    snapshot = service.snapshot()
    counters = snapshot.get("counters")
    gauges = snapshot.get("gauges")
    assert isinstance(counters, dict)
    assert isinstance(gauges, dict)

    assert counters["dispatch_submitted_total"] == 11
    assert counters["dispatch_processed_total"] == 7
    assert counters["dispatch_failed_total"] == 1
    assert counters["dispatch_dropped_total"] == 2
    assert counters["dispatch_submit_dropped_total"] == 5
    assert counters["dispatch_submit_timeout_total"] == 1
    assert counters["dispatch_submit_block_wait_ms_total"] == 125
    assert counters["sink_exported_total"] == 17
    assert counters["sink_dropped_total"] == 3
    assert counters["loss_estimate_total"] == 10  # dispatch_dropped + dispatch_submit_dropped_total + sink_dropped_total

    assert gauges["dispatch_queue_depth"] == 3
    assert gauges["dispatch_pending"] == 3
    assert gauges["pending_total_estimate"] == 7
    assert gauges["sink_count"] == 2
    assert gauges["sink_pending_total"] == 4
    assert gauges["tracing_enabled"] == 1


def test_observability_metrics_service_metric_records_have_stable_names_and_labels() -> None:
    service = InMemoryObservabilityMetricsService()
    service.ingest_trace_dispatch_snapshot(stage="stop_requested", snapshot=_snapshot_fixture())

    records = service.metric_records()
    assert records
    assert records == sorted(records, key=lambda item: str(item.get("name")))
    first = records[0]
    assert set(first) == {"name", "type", "value", "labels"}
    labels = first["labels"]
    assert isinstance(labels, dict)
    assert labels.get("scope") == "observability_transport"
    assert labels.get("component") == "trace_dispatch"


def test_observability_metrics_service_tracks_lifecycle_stages() -> None:
    service = InMemoryObservabilityMetricsService()
    service.ingest_trace_dispatch_snapshot(stage="stop_requested", snapshot=_snapshot_fixture())
    service.ingest_trace_dispatch_snapshot(stage="close_begin", snapshot=_snapshot_fixture())
    service.ingest_trace_dispatch_snapshot(stage="close_end", snapshot=_snapshot_fixture())

    snapshot = service.snapshot()
    lifecycle = snapshot.get("lifecycle")
    counters = snapshot.get("counters")
    assert isinstance(lifecycle, dict)
    assert isinstance(counters, dict)
    assert lifecycle.get("last_stage") == "close_end"
    stages = lifecycle.get("stages")
    assert isinstance(stages, dict)
    assert stages.get("stop_requested") is True
    assert stages.get("close_begin") is True
    assert stages.get("close_end") is True
    assert counters["lifecycle_event_total"] == 3
    assert counters["stage_stop_requested_total"] == 1
    assert counters["stage_close_begin_total"] == 1
    assert counters["stage_close_end_total"] == 1


def test_observability_metrics_dispatch_service_uses_metrics_service_snapshot_contract() -> None:
    metrics = InMemoryObservabilityMetricsService()
    dispatch = DefaultObservabilityMetricsDispatchService(metrics_service=metrics)

    result = dispatch.dispatch_snapshot(
        event=MonitoringMetricsSnapshotEvent(
            stage="stop_requested",
            snapshot=_snapshot_fixture(),
        )
    )

    assert result.stage == "stop_requested"
    assert result.snapshot.get("dispatch_submitted") == 11
    assert result.metric_records
    names = {str(item.get("name")) for item in result.metric_records}
    assert "stream_kernel_observability_dispatch_submitted_total" in names
    assert "stream_kernel_observability_pending_total_estimate" in names
