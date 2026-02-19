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
