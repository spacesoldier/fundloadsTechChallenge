from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Callable

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.monitoring import MonitoringMessage


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class StdoutMonitoringSink:
    # Minimal monitoring sink for runtime health/status events.
    def emit(self, message: MonitoringMessage) -> None:
        print(f"{message.name}:{message.status}")


class JsonlMonitoringSink:
    # JSONL monitoring sink for time-series style offline analysis/import.
    def __init__(
        self,
        *,
        path: str,
        flush_every_n: int = 1,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")
        self._lock = Lock()
        self._emit_count = 0
        self._flush_every_n = max(1, int(flush_every_n))
        self._fsync_every_n = fsync_every_n if isinstance(fsync_every_n, int) and fsync_every_n > 0 else None
        self._failed = 0

    def emit(self, message: MonitoringMessage) -> None:
        payload = {
            "kind": "monitoring_message",
            "name": message.name,
            "status": message.status,
            "timestamp": message.timestamp.isoformat(),
            "details": dict(message.details),
        }
        self._write_json_line(payload)

    async def emit_async(self, message: MonitoringMessage) -> None:
        self.emit(message)

    def publish_metrics(
        self,
        *,
        records: list[dict[str, object]],
        snapshot: dict[str, object] | None = None,
        stage: str | None = None,
    ) -> None:
        payload = {
            "kind": "monitoring_metrics_snapshot",
            "stage": stage or "runtime",
            "ts_epoch_ms": int(time.time() * 1000),
            "snapshot": dict(snapshot) if isinstance(snapshot, dict) else {},
            "records": [dict(record) for record in records if isinstance(record, dict)],
        }
        self._write_json_line(payload)

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "path": str(self._path),
                "exported": self._emit_count,
                "failed": self._failed,
            }

    def flush(self) -> None:
        with self._lock:
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.flush()
            self._handle.close()

    def _write_json_line(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=_json_default)
        with self._lock:
            try:
                self._handle.write(line + "\n")
                next_emit = self._emit_count + 1
                if next_emit % self._flush_every_n == 0:
                    self._handle.flush()
                if self._fsync_every_n and next_emit % self._fsync_every_n == 0:
                    self._handle.flush()
                    os.fsync(self._handle.fileno())
                self._emit_count = next_emit
            except Exception:
                self._failed += 1


@dataclass(slots=True)
class _PrometheusRendered:
    payload: str
    metric_count: int


class PrometheusMonitoringSink:
    # Prometheus monitoring exporter adapter:
    # - http_pull serves latest rendered metrics;
    # - textfile writes deterministic .prom snapshots.
    def __init__(
        self,
        *,
        mode: str = "http_pull",
        namespace: str | None = "stream_kernel",
        subsystem: str | None = "observability",
        include_labels: dict[str, bool] | None = None,
        http_host: str = "127.0.0.1",
        http_port: int = 9464,
        http_path: str = "/metrics",
        textfile_path: str = "metrics/stream_kernel.prom",
        textfile_write_every_n: int = 100,
        textfile_write_interval_ms: int = 250,
    ) -> None:
        self._mode = mode
        self._namespace = namespace.strip() if isinstance(namespace, str) else ""
        self._subsystem = subsystem.strip() if isinstance(subsystem, str) else ""
        self._include_labels = dict(include_labels or {})
        self._http_host = http_host
        self._http_port = int(http_port)
        self._http_path = http_path if isinstance(http_path, str) and http_path else "/metrics"
        self._textfile_path = Path(textfile_path)
        self._textfile_write_every_n = max(1, int(textfile_write_every_n))
        self._textfile_write_interval_seconds = max(0.0, float(textfile_write_interval_ms) / 1000.0)
        self._render_lock = Lock()
        self._rendered = _PrometheusRendered(payload="", metric_count=0)
        self._snapshot_records: dict[str, dict[str, object]] = {}
        self._message_records: dict[str, dict[str, object]] = {}
        self._worker_sample_counters: dict[tuple[str, str, str, str], int] = {}
        self._textfile_dirty = False
        self._textfile_pending_updates = 0
        self._last_textfile_write_monotonic = time.monotonic()
        self._exported = 0
        self._failed = 0
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: Thread | None = None

        if self._mode == "http_pull":
            self._start_http_server()

    def emit(self, message: MonitoringMessage) -> None:
        try:
            message_records = self._records_from_message(message)
            with self._render_lock:
                for record in message_records:
                    self._message_records[self._record_key(record)] = dict(record)
                self._refresh_rendered_locked()
                self._exported += 1
        except Exception:
            with self._render_lock:
                self._failed += 1
            return None

    def publish_metrics(
        self,
        *,
        records: list[dict[str, object]],
        snapshot: dict[str, object] | None = None,
        stage: str | None = None,
    ) -> None:
        _ = (snapshot, stage)
        try:
            with self._render_lock:
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    self._snapshot_records[self._record_key(record)] = dict(record)
                self._refresh_rendered_locked()
                self._exported += 1
        except Exception:
            with self._render_lock:
                self._failed += 1
            return None

    def diagnostics(self) -> dict[str, object]:
        with self._render_lock:
            payload = self._rendered.payload
            metric_count = self._rendered.metric_count
            exported = self._exported
            failed = self._failed
        details: dict[str, object] = {
            "mode": self._mode,
            "exported": exported,
            "failed": failed,
            "metric_count": metric_count,
            "payload_bytes": len(payload.encode("utf-8")),
        }
        if self._mode == "http_pull":
            details["http_host"] = self._http_host
            details["http_port"] = self._http_port
            details["http_path"] = self._http_path
        else:
            details["textfile_path"] = str(self._textfile_path)
        return details

    def close(self) -> None:
        if self._mode == "textfile":
            with self._render_lock:
                self._write_textfile_locked(force=True)
        server = self._http_server
        thread = self._http_thread
        self._http_server = None
        self._http_thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def flush(self) -> None:
        if self._mode != "textfile":
            return
        with self._render_lock:
            self._write_textfile_locked(force=True)

    def _start_http_server(self) -> None:
        sink = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                if self.path != sink._http_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = sink._rendered_payload()
                body = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - stdlib signature
                _ = (format, args)
                return None

        try:
            server = _ReusableThreadingHTTPServer((self._http_host, self._http_port), _Handler)
        except OSError as exc:
            raise ValueError(
                "monitoring_prometheus failed to bind http endpoint "
                f"{self._http_host}:{self._http_port}"
            ) from exc
        self._http_server = server
        self._http_host = str(server.server_address[0])
        self._http_port = int(server.server_address[1])
        thread = Thread(target=server.serve_forever, name=f"prometheus.http.{self._http_port}", daemon=True)
        self._http_thread = thread
        thread.start()

    def _rendered_payload(self) -> str:
        with self._render_lock:
            return self._rendered.payload

    def _refresh_rendered_locked(self) -> None:
        merged_records = [
            *self._snapshot_records.values(),
            *self._message_records.values(),
        ]
        rendered = self._render(merged_records)
        self._rendered = rendered
        if self._mode == "textfile":
            self._textfile_dirty = True
            self._textfile_pending_updates += 1
            self._write_textfile_locked(force=False)

    def _write_textfile_locked(self, *, force: bool) -> None:
        if self._mode != "textfile":
            return
        if not self._textfile_dirty and not force:
            return
        if not force:
            now = time.monotonic()
            if (
                self._textfile_pending_updates < self._textfile_write_every_n
                and (now - self._last_textfile_write_monotonic) < self._textfile_write_interval_seconds
            ):
                return
        self._textfile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._rendered.payload
        self._textfile_path.write_text(payload, encoding="utf-8")
        self._textfile_dirty = False
        self._textfile_pending_updates = 0
        self._last_textfile_write_monotonic = time.monotonic()

    def _record_key(self, record: dict[str, object]) -> str:
        name = str(record.get("name", ""))
        metric_type = str(record.get("type", ""))
        labels = record.get("labels", {})
        if isinstance(labels, dict):
            labels_key = tuple(
                (str(key), str(value))
                for key, value in sorted(labels.items(), key=lambda item: str(item[0]))
                if isinstance(key, str)
            )
        else:
            labels_key = ()
        return f"{name}|{metric_type}|{labels_key}"

    def _records_from_message(self, message: MonitoringMessage) -> list[dict[str, object]]:
        details = dict(message.details)
        if message.name != "worker_queue_depth":
            labels = {"message_name": message.name, "status": message.status}
            return [
                {
                    "name": "monitoring_events_total",
                    "type": "counter",
                    "value": 1,
                    "labels": labels,
                }
            ]

        group_name = details.get("group_name")
        worker_id = details.get("worker_id")
        pid = details.get("pid")
        runner_profile = details.get("runner_profile")
        queue_depth = details.get("queue_depth")
        inflight = details.get("inflight")

        labels = {
            "group_name": str(group_name) if isinstance(group_name, str) else "unknown",
            "worker_id": str(worker_id) if isinstance(worker_id, str) else "unknown",
            "pid": str(pid) if isinstance(pid, int) else "0",
            "runner_profile": str(runner_profile) if isinstance(runner_profile, str) else "unknown",
        }
        key = (
            labels["group_name"],
            labels["worker_id"],
            labels["pid"],
            labels["runner_profile"],
        )
        next_samples = int(self._worker_sample_counters.get(key, 0)) + 1
        self._worker_sample_counters[key] = next_samples
        queue_depth_value = int(queue_depth) if isinstance(queue_depth, (int, float)) else 0
        inflight_value = int(inflight) if isinstance(inflight, (int, float)) else 0

        return [
            {
                "name": "worker_queue_depth",
                "type": "gauge",
                "value": max(0, queue_depth_value),
                "labels": dict(labels),
            },
            {
                "name": "worker_inflight",
                "type": "gauge",
                "value": max(0, inflight_value),
                "labels": dict(labels),
            },
            {
                "name": "worker_queue_samples_total",
                "type": "counter",
                "value": next_samples,
                "labels": dict(labels),
            },
        ]

    def _render(self, records: list[dict[str, object]]) -> _PrometheusRendered:
        type_by_metric: dict[str, str] = {}
        lines: list[str] = []
        metric_count = 0

        sorted_records = sorted(
            [record for record in records if isinstance(record, dict)],
            key=lambda record: str(record.get("name", "")),
        )
        for record in sorted_records:
            name = record.get("name")
            value = record.get("value")
            metric_type = record.get("type")
            labels = record.get("labels")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(metric_type, str) or metric_type not in {"counter", "gauge"}:
                continue
            if not isinstance(value, (int, float)):
                continue
            full_name = self._full_metric_name(name)
            labels_text = self._labels_text(labels)
            if full_name not in type_by_metric:
                type_by_metric[full_name] = metric_type
                lines.append(f"# TYPE {full_name} {metric_type}")
            lines.append(f"{full_name}{labels_text} {value}")
            metric_count += 1

        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        return _PrometheusRendered(payload=payload, metric_count=metric_count)

    def _full_metric_name(self, name: str) -> str:
        token = _metric_token(name)
        prefix_parts = [part for part in (self._namespace, self._subsystem) if part]
        prefix = "_".join(_metric_token(part) for part in prefix_parts if part)
        if prefix and token.startswith(prefix + "_"):
            return token
        if prefix:
            return f"{prefix}_{token}"
        return token

    def _labels_text(self, labels: object) -> str:
        if not isinstance(labels, dict) or not labels:
            return ""
        included: list[tuple[str, str]] = []
        for key in sorted(labels):
            value = labels.get(key)
            if not isinstance(key, str) or not key:
                continue
            policy = self._include_labels.get(key)
            if policy is False:
                continue
            if policy is None and self._include_labels and key not in self._include_labels:
                continue
            included.append((_metric_token(key), _escape_label_value(value)))
        if not included:
            return ""
        rendered = ",".join(f'{key}="{value}"' for key, value in included)
        return "{" + rendered + "}"


def _metric_token(value: str) -> str:
    token = "".join(char if char.isalnum() else "_" for char in value.strip().lower())
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_") or "metric"


def _escape_label_value(value: object) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _json_default(value: object) -> object:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            return str(value)
    return str(value)


@adapter(
    name="monitoring_jsonl",
    consumes=[MonitoringMessage],
    emits=[],
    binds=[("stream", MonitoringMessage)],
    execution_mode="async",
)
def monitoring_jsonl(settings: dict[str, object]) -> JsonlMonitoringSink:
    path = settings.get("path", "metrics/monitoring_timeseries.jsonl")
    if not isinstance(path, str) or not path:
        raise ValueError("monitoring_jsonl.settings.path must be a non-empty string")
    flush_every_n = settings.get("flush_every_n", 1)
    if not isinstance(flush_every_n, int) or flush_every_n <= 0:
        raise ValueError("monitoring_jsonl.settings.flush_every_n must be an integer > 0")
    fsync_every_n = settings.get("fsync_every_n")
    if fsync_every_n is not None and (not isinstance(fsync_every_n, int) or fsync_every_n <= 0):
        raise ValueError("monitoring_jsonl.settings.fsync_every_n must be an integer > 0 when provided")
    return JsonlMonitoringSink(path=path, flush_every_n=flush_every_n, fsync_every_n=fsync_every_n)


@adapter(
    name="monitoring_stdout",
    consumes=[MonitoringMessage],
    emits=[],
    binds=[("stream", MonitoringMessage)],
    execution_mode="async",
)
def monitoring_stdout(settings: dict[str, object]) -> StdoutMonitoringSink:
    # Framework-owned stdout monitoring sink over standard stream channel.
    _ = settings
    return StdoutMonitoringSink()


@adapter(
    name="monitoring_prometheus",
    consumes=[MonitoringMessage],
    emits=[],
    binds=[("stream", MonitoringMessage)],
    execution_mode="async",
)
def monitoring_prometheus(settings: dict[str, object]) -> PrometheusMonitoringSink:
    # Prometheus monitoring exporter (http pull or textfile) for runtime metrics snapshots.
    mode = settings.get("mode", "http_pull")
    if not isinstance(mode, str) or mode not in {"http_pull", "textfile"}:
        raise ValueError("monitoring_prometheus.settings.mode must be one of: ['http_pull', 'textfile']")

    include_labels = settings.get("include_labels", {})
    if not isinstance(include_labels, dict):
        raise ValueError("monitoring_prometheus.settings.include_labels must be a mapping when provided")
    label_policy: dict[str, bool] = {}
    for key, value in include_labels.items():
        if not isinstance(key, str) or not isinstance(value, bool):
            raise ValueError(
                "monitoring_prometheus.settings.include_labels must be a string-to-boolean mapping"
            )
        label_policy[key] = value

    namespace = settings.get("namespace", "stream_kernel")
    subsystem = settings.get("subsystem", "observability")

    http_cfg = settings.get("http", {})
    if not isinstance(http_cfg, dict):
        raise ValueError("monitoring_prometheus.settings.http must be a mapping when provided")
    textfile_cfg = settings.get("textfile", {})
    if not isinstance(textfile_cfg, dict):
        raise ValueError("monitoring_prometheus.settings.textfile must be a mapping when provided")

    host = http_cfg.get("host", "127.0.0.1")
    port = http_cfg.get("port", 9464)
    path = http_cfg.get("path", "/metrics")
    textfile_path = textfile_cfg.get("path", "metrics/stream_kernel.prom")
    textfile_write_every_n = textfile_cfg.get("write_every_n", 100)
    if not isinstance(textfile_write_every_n, int) or textfile_write_every_n <= 0:
        raise ValueError("monitoring_prometheus.settings.textfile.write_every_n must be an integer > 0")
    textfile_write_interval_ms = textfile_cfg.get("write_interval_ms", 250)
    if not isinstance(textfile_write_interval_ms, int) or textfile_write_interval_ms < 0:
        raise ValueError("monitoring_prometheus.settings.textfile.write_interval_ms must be an integer >= 0")

    if not isinstance(host, str) or not host:
        raise ValueError("monitoring_prometheus.settings.http.host must be a non-empty string")
    if not isinstance(port, int) or port <= 0:
        raise ValueError("monitoring_prometheus.settings.http.port must be an integer > 0")
    if not isinstance(path, str) or not path:
        raise ValueError("monitoring_prometheus.settings.http.path must be a non-empty string")
    if not isinstance(textfile_path, str) or not textfile_path:
        raise ValueError("monitoring_prometheus.settings.textfile.path must be a non-empty string")

    return PrometheusMonitoringSink(
        mode=mode,
        namespace=namespace if isinstance(namespace, str) else "stream_kernel",
        subsystem=subsystem if isinstance(subsystem, str) else "observability",
        include_labels=label_policy,
        http_host=host,
        http_port=port,
        http_path=path,
        textfile_path=textfile_path,
        textfile_write_every_n=textfile_write_every_n,
        textfile_write_interval_ms=textfile_write_interval_ms,
    )
