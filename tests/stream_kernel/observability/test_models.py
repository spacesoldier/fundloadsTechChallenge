from __future__ import annotations

import pytest

from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.telemetry import TelemetryMessage
from stream_kernel.observability.domain.tracing import TraceMessage


def test_trace_message_requires_non_empty_identity_fields() -> None:
    # Observability trace events must carry stable identity fields for routing and indexing.
    with pytest.raises(ValueError):
        TraceMessage(trace_id="", span="", status="ok")


def test_log_message_requires_level_and_message() -> None:
    # Logging payloads are structured but still require explicit semantic basics.
    with pytest.raises(ValueError):
        LogMessage(level="", message="")


def test_telemetry_message_requires_metric_name() -> None:
    # Telemetry events must always identify the metric name.
    with pytest.raises(ValueError):
        TelemetryMessage(metric="", value=1)
