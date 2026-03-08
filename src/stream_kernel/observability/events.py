from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True, slots=True)
class MonitoringMetricsSnapshotEvent:
    stage: str
    snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class MonitoringMetricsSnapshotResult:
    stage: str
    snapshot: dict[str, object]
    metric_records: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorkerQueueTelemetryEvent:
    group_name: str
    worker_id: str
    pid: int
    queue_depth: int
    inflight: int
    runner_profile: str
    ts_epoch_ms: int


@dataclass(frozen=True, slots=True)
class DebugDispatchEvent:
    payload: object
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)
