from __future__ import annotations

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
    ) -> None:
        self._mode = mode
        self._namespace = namespace.strip() if isinstance(namespace, str) else ""
        self._subsystem = subsystem.strip() if isinstance(subsystem, str) else ""
        self._include_labels = dict(include_labels or {})
        self._http_host = http_host
        self._http_port = int(http_port)
        self._http_path = http_path if isinstance(http_path, str) and http_path else "/metrics"
        self._textfile_path = Path(textfile_path)
        self._render_lock = Lock()
        self._rendered = _PrometheusRendered(payload="", metric_count=0)
        self._exported = 0
        self._failed = 0
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: Thread | None = None

        if self._mode == "http_pull":
            self._start_http_server()

    def emit(self, message: MonitoringMessage) -> None:
        _ = message
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
            rendered = self._render(records)
            if self._mode == "textfile":
                self._textfile_path.parent.mkdir(parents=True, exist_ok=True)
                self._textfile_path.write_text(rendered.payload, encoding="utf-8")
            with self._render_lock:
                self._rendered = rendered
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
    )
