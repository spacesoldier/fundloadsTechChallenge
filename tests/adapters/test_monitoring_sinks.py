from __future__ import annotations

from pathlib import Path

import pytest

import stream_kernel.observability.adapters.monitoring as monitoring_module
from stream_kernel.observability.adapters.monitoring import (
    PrometheusMonitoringSink,
    monitoring_prometheus,
)


def _records_fixture() -> list[dict[str, object]]:
    return [
        {
            "name": "dispatch_submitted_total",
            "type": "counter",
            "value": 11,
            "labels": {"scope": "observability_transport", "component": "trace_dispatch"},
        },
        {
            "name": "dispatch_queue_depth",
            "type": "gauge",
            "value": 3,
            "labels": {"scope": "observability_transport", "component": "trace_dispatch"},
        },
    ]


def test_monitoring_prometheus_textfile_writes_deterministic_exposition(tmp_path: Path) -> None:
    path = tmp_path / "metrics.prom"
    sink = monitoring_prometheus(
        {
            "mode": "textfile",
            "textfile": {"path": str(path)},
            "namespace": "stream_kernel",
            "subsystem": "observability",
        }
    )
    assert isinstance(sink, PrometheusMonitoringSink)
    sink.publish_metrics(records=_records_fixture(), snapshot={"k": "v"}, stage="stop_requested")
    sink.close()

    payload = path.read_text(encoding="utf-8")
    assert "# TYPE stream_kernel_observability_dispatch_submitted_total counter" in payload
    assert "# TYPE stream_kernel_observability_dispatch_queue_depth gauge" in payload
    assert 'stream_kernel_observability_dispatch_submitted_total{component="trace_dispatch",scope="observability_transport"} 11' in payload
    assert 'stream_kernel_observability_dispatch_queue_depth{component="trace_dispatch",scope="observability_transport"} 3' in payload


def test_monitoring_prometheus_http_pull_serves_latest_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {"serve_forever": 0, "shutdown": 0, "server_close": 0}

    class _FakeServer:
        def __init__(self, addr: tuple[str, int], _handler_cls: object) -> None:
            captured["bind_addr"] = addr
            self.server_address = (addr[0], 19464)

        def serve_forever(self) -> None:
            captured["serve_forever"] = int(captured["serve_forever"]) + 1

        def shutdown(self) -> None:
            captured["shutdown"] = int(captured["shutdown"]) + 1

        def server_close(self) -> None:
            captured["server_close"] = int(captured["server_close"]) + 1

    monkeypatch.setattr(monitoring_module, "_ReusableThreadingHTTPServer", _FakeServer)

    sink = monitoring_prometheus(
        {
            "mode": "http_pull",
            "http": {"host": "127.0.0.1", "port": 9464, "path": "/metrics"},
            "namespace": "stream_kernel",
            "subsystem": "observability",
        }
    )
    sink.publish_metrics(records=_records_fixture(), snapshot=None, stage="close_end")
    diagnostics = sink.diagnostics()
    payload = sink._rendered_payload()  # noqa: SLF001 - exporter rendering contract probe.
    sink.close()

    assert captured["bind_addr"] == ("127.0.0.1", 9464)
    assert int(captured["shutdown"]) == 1
    assert int(captured["server_close"]) == 1
    assert diagnostics.get("http_port") == 19464
    assert diagnostics.get("http_path") == "/metrics"
    assert "stream_kernel_observability_dispatch_submitted_total" in payload
    assert "stream_kernel_observability_dispatch_queue_depth" in payload


def test_monitoring_prometheus_export_failure_is_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "metrics.prom"
    sink = monitoring_prometheus(
        {
            "mode": "textfile",
            "textfile": {"path": str(path)},
        }
    )

    def _raise_write_text(self: Path, *_args: object, **_kwargs: object) -> str:
        if self == path:
            raise OSError("disk unavailable")
        return ""

    monkeypatch.setattr(Path, "write_text", _raise_write_text, raising=False)
    sink.publish_metrics(records=_records_fixture(), snapshot={"k": "v"}, stage="close_begin")
    diagnostics = sink.diagnostics()
    sink.close()
    assert diagnostics["exported"] == 0
    assert diagnostics["failed"] == 1
