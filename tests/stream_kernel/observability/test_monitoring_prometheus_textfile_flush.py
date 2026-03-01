from __future__ import annotations

from pathlib import Path

from stream_kernel.observability.adapters.monitoring import monitoring_prometheus
from stream_kernel.observability.domain.monitoring import MonitoringMessage


def test_prometheus_textfile_throttles_writes_until_flush(tmp_path: Path) -> None:
    path = tmp_path / "metrics.prom"
    sink = monitoring_prometheus(
        {
            "mode": "textfile",
            "namespace": "stream_kernel",
            "subsystem": "observability",
            "textfile": {
                "path": str(path),
                "write_every_n": 1000,
                "write_interval_ms": 60_000,
            },
        }
    )
    sink.emit(MonitoringMessage(name="runner.node", status="ok"))
    # Throttled textfile mode should avoid writing on every event in hot path.
    assert path.exists() is False
    sink.flush()
    assert path.exists() is True
    data = path.read_text(encoding="utf-8")
    assert "stream_kernel_observability_monitoring_events_total" in data
    sink.close()


def test_prometheus_textfile_close_forces_final_flush(tmp_path: Path) -> None:
    path = tmp_path / "metrics_close.prom"
    sink = monitoring_prometheus(
        {
            "mode": "textfile",
            "textfile": {
                "path": str(path),
                "write_every_n": 1000,
                "write_interval_ms": 60_000,
            },
        }
    )
    sink.emit(MonitoringMessage(name="runner.node", status="ok"))
    assert path.exists() is False
    sink.close()
    assert path.exists() is True
    data = path.read_text(encoding="utf-8")
    assert "monitoring_events_total" in data
