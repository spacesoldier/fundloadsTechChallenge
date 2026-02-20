from __future__ import annotations

from pathlib import Path
import json

import pytest

import stream_kernel.observability.adapters.monitoring as monitoring_module
from stream_kernel.observability.adapters.monitoring import (
    JsonlMonitoringSink,
    PrometheusMonitoringSink,
    monitoring_jsonl,
    monitoring_prometheus,
)
from stream_kernel.observability.domain.monitoring import MonitoringMessage


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


def test_monitoring_jsonl_writes_monitoring_messages_and_metrics_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "metrics" / "monitoring.jsonl"
    sink = monitoring_jsonl(
        {
            "path": str(path),
            "flush_every_n": 1,
        }
    )
    assert isinstance(sink, JsonlMonitoringSink)

    sink.emit(
        MonitoringMessage(
            name="worker_queue_depth",
            status="sample",
            details={"group_name": "execution.features", "queue_depth": 3},
        )
    )
    sink.publish_metrics(
        records=[{"name": "stream_kernel_observability_dispatch_queue_depth", "type": "gauge", "value": 2}],
        snapshot={"dispatch_queue_depth": 2},
        stage="stop_requested",
    )
    sink.close()

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0].get("kind") == "monitoring_message"
    assert lines[0].get("name") == "worker_queue_depth"
    assert lines[1].get("kind") == "monitoring_metrics_snapshot"
    assert lines[1].get("stage") == "stop_requested"


def test_monitoring_jsonl_emit_async_does_not_require_to_thread(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metrics" / "monitoring_async.jsonl"
    sink = monitoring_jsonl({"path": str(path), "flush_every_n": 1})

    assert not hasattr(monitoring_module, "asyncio")

    import asyncio

    asyncio.run(
        sink.emit_async(
            MonitoringMessage(
                name="worker_queue_depth",
                status="sample",
                details={"group_name": "execution.features", "queue_depth": 1},
            )
        )
    )
    sink.close()

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].get("kind") == "monitoring_message"


def test_monitoring_prometheus_textfile_updates_from_worker_queue_monitoring_messages(tmp_path: Path) -> None:
    path = tmp_path / "worker_queue.prom"
    sink = monitoring_prometheus(
        {
            "mode": "textfile",
            "textfile": {"path": str(path)},
            "namespace": "stream_kernel",
            "subsystem": "observability",
        }
    )
    sink.emit(
        MonitoringMessage(
            name="worker_queue_depth",
            status="sample",
            details={
                "group_name": "execution.features",
                "worker_id": "execution.features#1",
                "pid": 12345,
                "runner_profile": "sync",
                "queue_depth": 7,
                "inflight": 1,
            },
        )
    )
    sink.close()

    payload = path.read_text(encoding="utf-8")
    assert "stream_kernel_observability_worker_queue_depth" in payload
    assert "stream_kernel_observability_worker_inflight" in payload
    assert "stream_kernel_observability_worker_queue_samples_total" in payload
    assert 'group_name="execution.features"' in payload
    assert 'worker_id="execution.features#1"' in payload
