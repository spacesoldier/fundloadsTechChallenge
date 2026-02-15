from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import sys
import threading
from importlib import import_module
from collections.abc import Callable, Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal
from urllib import request as urllib_request

if TYPE_CHECKING:
    from stream_kernel.kernel.trace import TraceRecord


class JsonlTraceSink:
    # JsonlTraceSink writes one TraceRecord per line (trace runtime docs).
    def __init__(
        self,
        *,
        path: Path,
        write_mode: Literal["line", "batch"] = "line",
        flush_every_n: int = 1,
        flush_every_ms: int | None = None,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = path
        self._write_mode = write_mode
        self._flush_every_n = max(1, flush_every_n)
        self._flush_every_ms = flush_every_ms  # reserved, not used in sync runtime
        self._fsync_every_n = fsync_every_n
        self._emit_count = 0
        self._buffer: list[str] = []
        self._handle = self._path.open("a", encoding="utf-8")

    def emit(self, record: "TraceRecord") -> None:
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        if self._write_mode == "batch":
            self._buffer.append(line)
            if len(self._buffer) >= self._flush_every_n:
                self._write_lines(self._buffer)
                self._buffer.clear()
        else:
            self._write_lines([line])
            if self._emit_count % self._flush_every_n == 0:
                self.flush()
        self._emit_count += 1
        if self._fsync_every_n and self._emit_count % self._fsync_every_n == 0:
            os.fsync(self._handle.fileno())

    def flush(self) -> None:
        if self._buffer:
            self._write_lines(self._buffer)
            self._buffer.clear()
        self._handle.flush()

    def close(self) -> None:
        self.flush()
        self._handle.close()

    def _write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._handle.write(line + "\n")


class StdoutTraceSink:
    # StdoutTraceSink prints one JSON record per line for local debugging.
    def emit(self, record: "TraceRecord") -> None:
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        sys.stdout.write(line + "\n")

    def flush(self) -> None:
        sys.stdout.flush()

    def close(self) -> None:
        self.flush()


class NoOpTraceSink:
    # Deterministic degraded sink used when exporter dependency is explicitly allowed to be missing.
    def __init__(self, *, reason: str = "dependency_missing") -> None:
        self._reason = reason
        self._dropped = 0

    @property
    def reason(self) -> str:
        return self._reason

    def emit(self, _record: "TraceRecord") -> None:
        self._dropped += 1

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.flush()

    def diagnostics(self) -> dict[str, int | str]:
        return {"exported": 0, "dropped": self._dropped, "reason": self._reason}


class OTelOtlpTraceSink:
    # OTel OTLP-compatible trace sink adapter with isolated exporter failures.
    def __init__(
        self,
        *,
        endpoint: str,
        backend: str = "urllib",
        headers: dict[str, str] | None = None,
        service_name: str = "stream-kernel",
        service_namespace: str | None = None,
        service_version: str | None = None,
        service_instance_id: str | None = None,
        deployment_environment: str | None = None,
        service_name_by_process_group: bool = True,
        include_runtime_resource: bool = True,
        span_kind: str = "SPAN_KIND_INTERNAL",
        export_fn: Callable[[dict[str, object]], None] | None = None,
        timeout_seconds: float = 2.0,
        batch_max_items: int = 1,
        batch_flush_interval_ms: int = 0,
        queue_max_items: int = 10_000,
        queue_drop_policy: str = "drop_newest",
        retry_max_attempts: int = 0,
        retry_backoff_ms: int = 0,
        sleep_fn: Callable[[float], None] | None = None,
        httpx_mode: str = "sync",
        httpx_http2: bool = False,
        httpx_max_connections: int | None = None,
        httpx_max_keepalive_connections: int | None = None,
        grpc_insecure: bool = True,
        grpc_timeout_seconds: float | None = None,
        grpc_retryable_status_codes: tuple[str, ...] | None = None,
        urllib3_num_pools: int | None = None,
        urllib3_maxsize: int | None = None,
        urllib3_block: bool | None = None,
        urllib3_timeout_seconds: float | None = None,
        aiohttp_shutdown_timeout_seconds: float = 2.0,
        aiohttp_connector_limit: int | None = None,
        aiohttp_connector_limit_per_host: int | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._backend = backend
        self._headers = dict(headers or {})
        self._service_name = service_name
        self._service_namespace = service_namespace
        self._service_version = service_version
        self._service_instance_id = service_instance_id
        self._deployment_environment = deployment_environment
        self._service_name_by_process_group = service_name_by_process_group
        self._include_runtime_resource = include_runtime_resource
        self._span_kind = span_kind
        self._export_fn = export_fn
        self._timeout_seconds = float(timeout_seconds)
        self._batch_max_items = max(1, int(batch_max_items))
        self._batch_flush_interval_ms = max(0, int(batch_flush_interval_ms))
        self._queue_max_items = max(1, int(queue_max_items))
        self._queue_drop_policy = queue_drop_policy
        self._retry_max_attempts = max(0, int(retry_max_attempts))
        self._retry_backoff_ms = max(0, int(retry_backoff_ms))
        self._sleep_fn = sleep_fn if callable(sleep_fn) else _default_sleep
        self._httpx_mode = httpx_mode
        self._httpx_http2 = bool(httpx_http2)
        self._httpx_max_connections = httpx_max_connections
        self._httpx_max_keepalive_connections = httpx_max_keepalive_connections
        self._grpc_insecure = bool(grpc_insecure)
        self._grpc_timeout_seconds = grpc_timeout_seconds
        retryable_codes = grpc_retryable_status_codes or ("UNAVAILABLE", "DEADLINE_EXCEEDED")
        self._grpc_retryable_status_codes = {str(code).upper() for code in retryable_codes if isinstance(code, str)}
        self._urllib3_num_pools = urllib3_num_pools
        self._urllib3_maxsize = urllib3_maxsize
        self._urllib3_block = urllib3_block
        self._urllib3_timeout_seconds = urllib3_timeout_seconds
        self._aiohttp_shutdown_timeout_seconds = float(aiohttp_shutdown_timeout_seconds)
        self._aiohttp_connector_limit = aiohttp_connector_limit
        self._aiohttp_connector_limit_per_host = aiohttp_connector_limit_per_host
        self._time_fn = time_fn if callable(time_fn) else monotonic
        self._span_buffer: list[dict[str, object]] = []
        self._batch_started_at: float | None = None
        self._requests_session: object | None = None
        self._httpx_client: object | None = None
        self._grpc_channel: object | None = None
        self._grpc_stub: object | None = None
        self._urllib3_pool_manager: object | None = None
        self._aiohttp_session: object | None = None
        self._otel_sdk_module: object | None = None
        self._otel_sdk_provider: object | None = None
        self._otel_sdk_tracer: object | None = None
        self._exported = 0
        self._dropped = 0

    def emit(self, record: "TraceRecord") -> None:
        span = _trace_to_otel_span(
            record,
            endpoint=self._endpoint,
            headers=self._headers,
            service_name=self._service_name,
            service_name_by_process_group=self._service_name_by_process_group,
            span_kind=self._span_kind,
        )
        if len(self._span_buffer) >= self._queue_max_items:
            if self._queue_drop_policy == "drop_newest":
                self._dropped += 1
                return
            if self._queue_drop_policy == "drop_oldest":
                self._span_buffer.pop(0)
                self._dropped += 1
            elif self._queue_drop_policy == "block_with_timeout":
                self._dropped += 1
                return
            else:
                self._dropped += 1
                return
        self._span_buffer.append(span)
        now = self._time_fn()
        if self._batch_started_at is None:
            self._batch_started_at = now

        flush_by_count = len(self._span_buffer) >= self._batch_max_items
        flush_by_timer = (
            self._batch_flush_interval_ms > 0
            and self._batch_started_at is not None
            and (now - self._batch_started_at) * 1000 >= self._batch_flush_interval_ms
        )
        if flush_by_count or flush_by_timer:
            self._flush_batch()

    def flush(self) -> None:
        self._flush_batch()

    def close(self) -> None:
        self.flush()
        session = self._requests_session
        self._requests_session = None
        close_fn = getattr(session, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                return None
        self._close_httpx_client()
        self._close_grpc_channel()
        self._close_urllib3_pool_manager()
        self._close_aiohttp_session()
        self._close_otel_sdk_provider()

    def diagnostics(self) -> dict[str, int]:
        return {"exported": self._exported, "dropped": self._dropped}

    def _flush_batch(self) -> None:
        if not self._span_buffer:
            return
        batch = list(self._span_buffer)
        self._span_buffer.clear()
        self._batch_started_at = None

        if callable(self._export_fn):
            for span in batch:
                try:
                    self._export_fn(span)
                except Exception:
                    self._dropped += 1
                    continue
                self._exported += 1
            return

        try:
            self._post_http(batch)
        except Exception:
            self._dropped += len(batch)
            return
        self._exported += len(batch)

    def _post_http(self, spans: list[dict[str, object]]) -> None:
        if self._backend not in {"urllib", "requests", "httpx", "aiohttp", "urllib3", "grpcio", "otel_sdk"}:
            raise RuntimeError(f"otlp_backend_not_implemented:{self._backend}")
        payload = _spans_to_otlp_http_payload(
            spans,
            service_name=self._service_name,
            service_namespace=self._service_namespace,
            service_version=self._service_version,
            service_instance_id=self._service_instance_id,
            deployment_environment=self._deployment_environment,
            include_runtime_resource=self._include_runtime_resource,
        )
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        ).encode("utf-8")
        if self._backend == "requests":
            self._call_with_retry(lambda: self._post_http_requests(body))
            return
        if self._backend == "httpx":
            self._call_with_retry(lambda: self._post_http_httpx(body))
            return
        if self._backend == "grpcio":
            self._post_grpc(spans)
            return
        if self._backend == "urllib3":
            self._call_with_retry(lambda: self._post_http_urllib3(body))
            return
        if self._backend == "aiohttp":
            self._call_with_retry(lambda: self._post_http_aiohttp(body))
            return
        if self._backend == "otel_sdk":
            self._post_otel_sdk(spans)
            return
        self._call_with_retry(lambda: self._post_http_urllib(body))

    def _call_with_retry(self, operation: Callable[[], None]) -> None:
        retries_done = 0
        while True:
            try:
                operation()
                return
            except Exception:
                if retries_done >= self._retry_max_attempts:
                    raise
                retries_done += 1
                if self._retry_backoff_ms > 0:
                    self._sleep_fn(self._retry_backoff_ms / 1000.0)

    def _call_with_retry_if(
        self,
        operation: Callable[[], None],
        *,
        is_retriable: Callable[[Exception], bool],
    ) -> None:
        retries_done = 0
        while True:
            try:
                operation()
                return
            except Exception as exc:
                if retries_done >= self._retry_max_attempts or not is_retriable(exc):
                    raise
                retries_done += 1
                if self._retry_backoff_ms > 0:
                    self._sleep_fn(self._retry_backoff_ms / 1000.0)

    def _post_http_urllib(self, body: bytes) -> None:
        request = urllib_request.Request(
            self._endpoint,
            data=body,
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        for key, value in self._headers.items():
            request.add_header(key, value)
        with urllib_request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310 - configured endpoint
            status = getattr(response, "status", None)
            if not isinstance(status, int):
                getcode = getattr(response, "getcode", None)
                status = getcode() if callable(getcode) else 200
            if status >= 400:
                raise OSError(f"otlp_http_export_failed:{status}")

    def _post_http_requests(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", **self._headers}
        session = self._get_requests_session()
        post = getattr(session, "post", None)
        if not callable(post):
            raise RuntimeError("requests.Session.post is not callable")
        response = post(
            self._endpoint,
            data=body,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and status_code >= 400:
            raise OSError(f"otlp_http_export_failed:{status_code}")
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

    def _get_requests_session(self) -> object:
        if self._requests_session is not None:
            return self._requests_session
        requests_module = _import_requests_module()
        session_factory = getattr(requests_module, "Session", None)
        if not callable(session_factory):
            raise RuntimeError("requests.Session factory is not available")
        self._requests_session = session_factory()
        return self._requests_session

    def _post_http_httpx(self, body: bytes) -> None:
        if self._httpx_mode == "async":
            self._post_http_httpx_async(body)
            return
        self._post_http_httpx_sync(body)

    def _post_grpc(self, spans: list[dict[str, object]]) -> None:
        request = _spans_to_otlp_http_payload(
            spans,
            service_name=self._service_name,
            service_namespace=self._service_namespace,
            service_version=self._service_version,
            service_instance_id=self._service_instance_id,
            deployment_environment=self._deployment_environment,
            include_runtime_resource=self._include_runtime_resource,
        )
        timeout = (
            float(self._grpc_timeout_seconds)
            if isinstance(self._grpc_timeout_seconds, (int, float))
            else self._timeout_seconds
        )
        stub = self._get_grpc_stub()
        export = getattr(stub, "Export", None)
        if not callable(export):
            raise RuntimeError("gRPC trace stub must expose Export(request, timeout=...)")

        self._call_with_retry_if(
            lambda: export(request, timeout=timeout),
            is_retriable=self._is_grpc_retriable_error,
        )

    def _post_http_urllib3(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", **self._headers}
        manager = self._get_urllib3_pool_manager()
        request_fn = getattr(manager, "request", None)
        if not callable(request_fn):
            raise RuntimeError("urllib3.PoolManager.request is not callable")
        response = request_fn(
            "POST",
            self._endpoint,
            body=body,
            headers=headers,
            timeout=(
                float(self._urllib3_timeout_seconds)
                if isinstance(self._urllib3_timeout_seconds, (int, float))
                else self._timeout_seconds
            ),
        )
        status = getattr(response, "status", None)
        if isinstance(status, int) and status >= 400:
            raise OSError(f"otlp_http_export_failed:{status}")
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        release_conn = getattr(response, "release_conn", None)
        if callable(release_conn):
            release_conn()

    def _post_http_httpx_sync(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", **self._headers}
        client = self._get_httpx_sync_client()
        post = getattr(client, "post", None)
        if not callable(post):
            raise RuntimeError("httpx.Client.post is not callable")
        response = post(
            self._endpoint,
            content=body,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        self._validate_http_response(response)

    def _post_http_httpx_async(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", **self._headers}
        client = self._get_httpx_async_client()

        async def _send() -> None:
            post = getattr(client, "post", None)
            if not callable(post):
                raise RuntimeError("httpx.AsyncClient.post is not callable")
            response = await post(
                self._endpoint,
                content=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            self._validate_http_response(response)

        _run_async_blocking(_send())

    def _get_httpx_sync_client(self) -> object:
        if self._httpx_client is not None:
            return self._httpx_client
        httpx_module = _import_httpx_module()
        client_cls = getattr(httpx_module, "Client", None)
        if not callable(client_cls):
            raise RuntimeError("httpx.Client is not available")
        limits = self._build_httpx_limits(httpx_module)
        kwargs: dict[str, object] = {
            "http2": self._httpx_http2,
            "timeout": self._timeout_seconds,
        }
        if limits is not None:
            kwargs["limits"] = limits
        self._httpx_client = client_cls(**kwargs)
        return self._httpx_client

    def _get_httpx_async_client(self) -> object:
        if self._httpx_client is not None:
            return self._httpx_client
        httpx_module = _import_httpx_module()
        client_cls = getattr(httpx_module, "AsyncClient", None)
        if not callable(client_cls):
            raise RuntimeError("httpx.AsyncClient is not available")
        limits = self._build_httpx_limits(httpx_module)
        kwargs: dict[str, object] = {
            "http2": self._httpx_http2,
            "timeout": self._timeout_seconds,
        }
        if limits is not None:
            kwargs["limits"] = limits
        self._httpx_client = client_cls(**kwargs)
        return self._httpx_client

    def _build_httpx_limits(self, httpx_module: object) -> object | None:
        has_max_connections = isinstance(self._httpx_max_connections, int) and self._httpx_max_connections > 0
        has_keepalive = (
            isinstance(self._httpx_max_keepalive_connections, int)
            and self._httpx_max_keepalive_connections > 0
        )
        if not has_max_connections and not has_keepalive:
            return None
        limits_cls = getattr(httpx_module, "Limits", None)
        if not callable(limits_cls):
            return None
        kwargs: dict[str, int] = {}
        if has_max_connections:
            kwargs["max_connections"] = int(self._httpx_max_connections)
        if has_keepalive:
            kwargs["max_keepalive_connections"] = int(self._httpx_max_keepalive_connections)
        return limits_cls(**kwargs)

    def _close_httpx_client(self) -> None:
        client = self._httpx_client
        self._httpx_client = None
        if client is None:
            return
        if self._httpx_mode == "async":
            async def _close_async() -> None:
                close_async = getattr(client, "aclose", None)
                if callable(close_async):
                    await close_async()

            try:
                _run_async_blocking(_close_async())
            except Exception:
                return
            return
        close_sync = getattr(client, "close", None)
        if callable(close_sync):
            try:
                close_sync()
            except Exception:
                return

    def _get_grpc_stub(self) -> object:
        if self._grpc_stub is not None:
            return self._grpc_stub
        grpc_module = _import_grpc_module()
        channel = self._get_grpc_channel()
        stub_cls = getattr(grpc_module, "OTLPTraceServiceStub", None)
        if not callable(stub_cls):
            stub_cls = getattr(grpc_module, "TraceServiceStub", None)
        if not callable(stub_cls):
            raise RuntimeError("gRPC module does not provide OTLP trace stub class")
        self._grpc_stub = stub_cls(channel)
        return self._grpc_stub

    def _get_grpc_channel(self) -> object:
        if self._grpc_channel is not None:
            return self._grpc_channel
        grpc_module = _import_grpc_module()
        endpoint = self._endpoint
        if self._grpc_insecure:
            factory = getattr(grpc_module, "insecure_channel", None)
            if not callable(factory):
                raise RuntimeError("grpc.insecure_channel is not available")
            self._grpc_channel = factory(endpoint)
            return self._grpc_channel
        secure_factory = getattr(grpc_module, "secure_channel", None)
        if not callable(secure_factory):
            raise RuntimeError("grpc.secure_channel is not available")
        creds_factory = getattr(grpc_module, "ssl_channel_credentials", None)
        credentials = creds_factory() if callable(creds_factory) else None
        self._grpc_channel = secure_factory(endpoint, credentials)
        return self._grpc_channel

    def _close_grpc_channel(self) -> None:
        channel = self._grpc_channel
        self._grpc_channel = None
        self._grpc_stub = None
        if channel is None:
            return
        close_fn = getattr(channel, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                return

    def _is_grpc_retriable_error(self, exc: Exception) -> bool:
        code_fn = getattr(exc, "code", None)
        if not callable(code_fn):
            return False
        try:
            code = code_fn()
        except Exception:
            return False
        if code is None:
            return False
        name = getattr(code, "name", None)
        code_name = name if isinstance(name, str) else str(code)
        normalized = code_name.split(".")[-1].upper()
        return normalized in self._grpc_retryable_status_codes

    def _get_urllib3_pool_manager(self) -> object:
        if self._urllib3_pool_manager is not None:
            return self._urllib3_pool_manager
        urllib3_module = _import_urllib3_module()
        pool_manager_cls = getattr(urllib3_module, "PoolManager", None)
        if not callable(pool_manager_cls):
            raise RuntimeError("urllib3.PoolManager is not available")
        kwargs: dict[str, object] = {}
        if isinstance(self._urllib3_num_pools, int) and self._urllib3_num_pools > 0:
            kwargs["num_pools"] = int(self._urllib3_num_pools)
        if isinstance(self._urllib3_maxsize, int) and self._urllib3_maxsize > 0:
            kwargs["maxsize"] = int(self._urllib3_maxsize)
        if isinstance(self._urllib3_block, bool):
            kwargs["block"] = self._urllib3_block
        self._urllib3_pool_manager = pool_manager_cls(**kwargs)
        return self._urllib3_pool_manager

    def _close_urllib3_pool_manager(self) -> None:
        manager = self._urllib3_pool_manager
        self._urllib3_pool_manager = None
        if manager is None:
            return
        clear_fn = getattr(manager, "clear", None)
        if callable(clear_fn):
            try:
                clear_fn()
            except Exception:
                return

    @staticmethod
    def _validate_http_response(response: object) -> None:
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and status_code >= 400:
            raise OSError(f"otlp_http_export_failed:{status_code}")
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

    def _post_http_aiohttp(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", **self._headers}
        session = self._get_aiohttp_session()

        async def _send() -> None:
            post = getattr(session, "post", None)
            if not callable(post):
                raise RuntimeError("aiohttp.ClientSession.post is not callable")
            async with post(
                self._endpoint,
                data=body,
                headers=headers,
                timeout=self._timeout_seconds,
            ) as response:
                status = getattr(response, "status", None)
                if isinstance(status, int) and status >= 400:
                    raise OSError(f"otlp_http_export_failed:{status}")
                raise_for_status = getattr(response, "raise_for_status", None)
                if callable(raise_for_status):
                    raise_for_status()

        _run_async_blocking(_send())

    def _get_aiohttp_session(self) -> object:
        if self._aiohttp_session is not None:
            return self._aiohttp_session
        aiohttp_module = _import_aiohttp_module()
        session_cls = getattr(aiohttp_module, "ClientSession", None)
        if not callable(session_cls):
            raise RuntimeError("aiohttp.ClientSession is not available")
        kwargs: dict[str, object] = {}
        connector = self._build_aiohttp_connector(aiohttp_module)
        if connector is not None:
            kwargs["connector"] = connector
        self._aiohttp_session = session_cls(**kwargs)
        return self._aiohttp_session

    def _build_aiohttp_connector(self, aiohttp_module: object) -> object | None:
        has_limit = isinstance(self._aiohttp_connector_limit, int) and self._aiohttp_connector_limit > 0
        has_limit_per_host = (
            isinstance(self._aiohttp_connector_limit_per_host, int)
            and self._aiohttp_connector_limit_per_host > 0
        )
        if not has_limit and not has_limit_per_host:
            return None
        connector_cls = getattr(aiohttp_module, "TCPConnector", None)
        if not callable(connector_cls):
            return None
        kwargs: dict[str, int] = {}
        if has_limit:
            kwargs["limit"] = int(self._aiohttp_connector_limit)
        if has_limit_per_host:
            kwargs["limit_per_host"] = int(self._aiohttp_connector_limit_per_host)
        return connector_cls(**kwargs)

    def _close_aiohttp_session(self) -> None:
        session = self._aiohttp_session
        self._aiohttp_session = None
        if session is None:
            return

        async def _close() -> None:
            close_fn = getattr(session, "close", None)
            if callable(close_fn):
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result

        try:
            _run_async_blocking(_close(), timeout_seconds=self._aiohttp_shutdown_timeout_seconds)
        except Exception:
            return

    def _post_otel_sdk(self, spans: list[dict[str, object]]) -> None:
        tracer = self._get_otel_sdk_tracer()
        start_span = getattr(tracer, "start_span", None)
        if not callable(start_span):
            raise RuntimeError("OpenTelemetry tracer must expose start_span(name, context=...)")
        for span in spans:
            span_name = str(span.get("name", "stream_kernel.step"))
            context = self._build_otel_sdk_context(span)
            sdk_span = start_span(span_name, context=context)
            set_attribute = getattr(sdk_span, "set_attribute", None)
            attrs = span.get("attributes", {})
            if callable(set_attribute) and isinstance(attrs, dict):
                for key, value in attrs.items():
                    if value is None:
                        continue
                    set_attribute(str(key), value)
            end_fn = getattr(sdk_span, "end", None)
            if callable(end_fn):
                end_fn()

    def _get_otel_sdk_tracer(self) -> object:
        if self._otel_sdk_tracer is not None:
            return self._otel_sdk_tracer
        module = self._get_otel_sdk_module()
        provider_cls = getattr(module, "TracerProvider", None)
        if not callable(provider_cls):
            raise RuntimeError("OpenTelemetry SDK module must provide TracerProvider")
        exporter = self._build_otel_sdk_exporter(module)
        processor_cls = getattr(module, "BatchSpanProcessor", None)
        if not callable(processor_cls):
            raise RuntimeError("OpenTelemetry SDK module must provide BatchSpanProcessor")
        processor_kwargs = self._build_otel_sdk_processor_kwargs()
        processor = processor_cls(exporter, **processor_kwargs)
        provider = provider_cls()
        add_processor = getattr(provider, "add_span_processor", None)
        if not callable(add_processor):
            raise RuntimeError("OpenTelemetry TracerProvider must expose add_span_processor")
        add_processor(processor)
        get_tracer = getattr(provider, "get_tracer", None)
        if not callable(get_tracer):
            raise RuntimeError("OpenTelemetry TracerProvider must expose get_tracer")
        self._otel_sdk_provider = provider
        self._otel_sdk_tracer = get_tracer("stream-kernel")
        return self._otel_sdk_tracer

    def _build_otel_sdk_exporter(self, module: object) -> object:
        exporter_cls = getattr(module, "OTLPHTTPSpanExporter", None)
        if not callable(exporter_cls):
            exporter_cls = getattr(module, "OTLPSpanExporter", None)
        if not callable(exporter_cls):
            raise RuntimeError("OpenTelemetry SDK module must provide OTLP span exporter")
        kwargs: dict[str, object] = {"endpoint": self._endpoint}
        if self._headers:
            kwargs["headers"] = dict(self._headers)
        if self._timeout_seconds > 0:
            kwargs["timeout"] = self._timeout_seconds
        return exporter_cls(**kwargs)

    def _build_otel_sdk_processor_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {"max_export_batch_size": self._batch_max_items}
        if self._queue_max_items > 0:
            kwargs["max_queue_size"] = self._queue_max_items
        if self._batch_flush_interval_ms > 0:
            kwargs["schedule_delay_millis"] = self._batch_flush_interval_ms
        return kwargs

    def _build_otel_sdk_context(self, span: dict[str, object]) -> object | None:
        module = self._get_otel_sdk_module()
        builder = getattr(module, "build_context", None)
        if not callable(builder):
            return None
        trace_id = span.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            return None
        span_id = span.get("span_id")
        parent_span_id = span.get("parent_span_id")
        return builder(
            trace_id=trace_id,
            span_id=span_id if isinstance(span_id, str) and span_id else None,
            parent_span_id=(
                parent_span_id if isinstance(parent_span_id, str) and parent_span_id else None
            ),
        )

    def _get_otel_sdk_module(self) -> object:
        if self._otel_sdk_module is not None:
            return self._otel_sdk_module
        self._otel_sdk_module = _import_otel_sdk_module()
        return self._otel_sdk_module

    def _close_otel_sdk_provider(self) -> None:
        provider = self._otel_sdk_provider
        self._otel_sdk_provider = None
        self._otel_sdk_tracer = None
        self._otel_sdk_module = None
        if provider is None:
            return
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            try:
                force_flush()
            except Exception:
                return
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                return


class OpenTracingBridgeTraceSink:
    # OpenTracing bridge sink for legacy tracer compatibility mode.
    def __init__(
        self,
        *,
        bridge_name: str = "opentracing",
        emit_fn: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._bridge_name = bridge_name
        self._emit_fn = emit_fn
        self._exported = 0
        self._dropped = 0

    def emit(self, record: "TraceRecord") -> None:
        span = _trace_to_opentracing_span(record, bridge_name=self._bridge_name)
        if callable(self._emit_fn):
            try:
                self._emit_fn(span)
            except Exception:
                self._dropped += 1
                return
        self._exported += 1

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.flush()

    def diagnostics(self) -> dict[str, int]:
        return {"exported": self._exported, "dropped": self._dropped}


def _trace_to_dict(record: "TraceRecord") -> dict[str, object]:
    # Keep key order stable so diffs stay deterministic in tests and diagnostics.
    return {
        "trace_id": record.trace_id,
        "scenario": record.scenario,
        "step_index": record.step_index,
        "step_name": record.step_name,
        "work_index": record.work_index,
        "t_enter": _format_dt(record.t_enter),
        "t_exit": _format_dt(record.t_exit),
        "duration_ms": record.duration_ms,
        "msg_in": _as_dict(record.msg_in),
        "msg_out": [_as_dict(item) for item in record.msg_out],
        "msg_out_count": record.msg_out_count,
        "ctx_before": record.ctx_before,
        "ctx_after": record.ctx_after,
        "ctx_diff": record.ctx_diff,
        "status": record.status,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "error": _as_dict(record.error) if record.error is not None else None,
        "route": _as_dict(record.route) if record.route is not None else None,
    }


def _trace_to_otel_span(
    record: "TraceRecord",
    *,
    endpoint: str,
    headers: dict[str, str],
    service_name: str,
    service_name_by_process_group: bool,
    span_kind: str,
) -> dict[str, object]:
    process_group = record.route.process_group if record.route is not None else None
    resolved_service_name = (
        f"{service_name}.{process_group}"
        if service_name_by_process_group and isinstance(process_group, str) and process_group
        else service_name
    )
    return {
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "name": record.step_name,
        "kind": span_kind,
        "start_time_unix_nano": int(record.t_enter.timestamp() * 1_000_000_000),
        "end_time_unix_nano": int(record.t_exit.timestamp() * 1_000_000_000),
        "status": record.status,
        "resource": {"service.name": resolved_service_name},
        "attributes": {
            "scenario": record.scenario,
            "step_index": record.step_index,
            "work_index": record.work_index,
            "msg_out_count": record.msg_out_count,
            "process_group": process_group,
            "handoff_from": record.route.handoff_from if record.route is not None else None,
            "route_hop": record.route.route_hop if record.route is not None else None,
            "stream_kernel.trace_id": record.trace_id,
            "stream_kernel.span_id": record.span_id,
            "stream_kernel.parent_span_id": record.parent_span_id,
        },
        "transport": {
            "endpoint": endpoint,
            "headers": dict(headers),
        },
    }


def _span_to_otlp_http_payload(
    span: dict[str, object],
    *,
    service_name: str,
    service_namespace: str | None = None,
    service_version: str | None = None,
    service_instance_id: str | None = None,
    deployment_environment: str | None = None,
    include_runtime_resource: bool = True,
) -> dict[str, object]:
    return _spans_to_otlp_http_payload(
        [span],
        service_name=service_name,
        service_namespace=service_namespace,
        service_version=service_version,
        service_instance_id=service_instance_id,
        deployment_environment=deployment_environment,
        include_runtime_resource=include_runtime_resource,
    )


def _spans_to_otlp_http_payload(
    spans: list[dict[str, object]],
    *,
    service_name: str,
    service_namespace: str | None = None,
    service_version: str | None = None,
    service_instance_id: str | None = None,
    deployment_environment: str | None = None,
    include_runtime_resource: bool = True,
) -> dict[str, object]:
    resource_spans: list[dict[str, object]] = []
    for span in spans:
        trace_id_text = str(span.get("trace_id", ""))
        span_id_text = span.get("span_id")
        parent_span_id_text = span.get("parent_span_id")
        span_name = str(span.get("name", "stream_kernel.step"))
        start_ns = int(span.get("start_time_unix_nano", 0))
        end_ns = int(span.get("end_time_unix_nano", start_ns))
        status = str(span.get("status", "ok")).lower()
        span_kind = str(span.get("kind", "SPAN_KIND_INTERNAL"))
        raw_attrs = span.get("attributes", {})
        attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
        raw_resource = span.get("resource", {})
        resource = raw_resource if isinstance(raw_resource, dict) else {}

        trace_hex = _stable_hex_id(trace_id_text, size_bytes=16)
        span_hex = _normalize_span_id(span_id_text) or _stable_hex_id(
            f"{trace_id_text}:{span_name}:{start_ns}:{end_ns}",
            size_bytes=8,
        )
        parent_hex = _normalize_span_id(parent_span_id_text)

        span_attrs = [
            _otlp_attr("stream_kernel.trace_id", trace_id_text),
            *[_otlp_attr(key, value) for key, value in attrs.items() if value is not None],
        ]
        status_code = (
            "STATUS_CODE_OK"
            if status == "ok"
            else "STATUS_CODE_ERROR" if status == "error" else "STATUS_CODE_UNSET"
        )

        resource_attributes = [_otlp_attr("service.name", str(resource.get("service.name", service_name)))]
        if isinstance(service_namespace, str) and service_namespace:
            resource_attributes.append(_otlp_attr("service.namespace", service_namespace))
        if isinstance(service_version, str) and service_version:
            resource_attributes.append(_otlp_attr("service.version", service_version))
        if isinstance(service_instance_id, str) and service_instance_id:
            resource_attributes.append(_otlp_attr("service.instance.id", service_instance_id))
        if isinstance(deployment_environment, str) and deployment_environment:
            resource_attributes.append(_otlp_attr("deployment.environment.name", deployment_environment))
        if include_runtime_resource:
            resource_attributes.extend(_runtime_resource_attributes())

        otlp_span: dict[str, object] = {
            "traceId": trace_hex,
            "spanId": span_hex,
            "kind": span_kind,
            "name": span_name,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": span_attrs,
            "status": {"code": status_code},
        }
        if isinstance(parent_hex, str):
            otlp_span["parentSpanId"] = parent_hex

        resource_spans.append(
            {
                "resource": {"attributes": resource_attributes},
                "scopeSpans": [
                    {
                        "scope": {"name": "stream-kernel"},
                        "spans": [otlp_span],
                    }
                ],
            }
        )

    return {"resourceSpans": resource_spans}


def _import_requests_module() -> object:
    return import_module("requests")


def _import_httpx_module() -> object:
    return import_module("httpx")


def _import_grpc_module() -> object:
    return import_module("grpc")


def _import_urllib3_module() -> object:
    return import_module("urllib3")


def _import_aiohttp_module() -> object:
    return import_module("aiohttp")


def _import_otel_sdk_module() -> object:
    # Imported lazily so OpenTelemetry SDK remains an optional dependency.
    module = import_module("opentelemetry.sdk.trace")
    exporter_http = import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    processor_mod = module
    provider_cls = getattr(module, "TracerProvider", None)
    processor_cls = getattr(processor_mod, "BatchSpanProcessor", None)
    exporter_cls = getattr(exporter_http, "OTLPSpanExporter", None)

    class _SDK:
        TracerProvider = provider_cls
        BatchSpanProcessor = processor_cls
        OTLPHTTPSpanExporter = exporter_cls

    return _SDK()


def check_otel_backend_dependencies(backend: str) -> None:
    # Eager backend dependency probe used for deterministic startup diagnostics.
    probes: dict[str, Callable[[], object]] = {
        "urllib": lambda: object(),
        "requests": _import_requests_module,
        "httpx": _import_httpx_module,
        "aiohttp": _import_aiohttp_module,
        "urllib3": _import_urllib3_module,
        "grpcio": _import_grpc_module,
        "otel_sdk": _import_otel_sdk_module,
    }
    probe = probes.get(backend)
    if probe is None:
        return
    try:
        probe()
    except Exception as exc:  # noqa: BLE001 - keep deterministic diagnostics text.
        raise RuntimeError(f"otlp_backend_dependency_missing:{backend}:{type(exc).__name__}") from exc


def _default_sleep(seconds: float) -> None:
    from time import sleep

    sleep(seconds)


def _run_async_blocking(coro: object, *, timeout_seconds: float | None = None) -> object:
    if not asyncio.iscoroutine(coro):
        raise TypeError("expected coroutine")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        if isinstance(timeout_seconds, float) and timeout_seconds > 0:
            async def _with_timeout() -> object:
                return await asyncio.wait_for(coro, timeout=timeout_seconds)

            return asyncio.run(_with_timeout())
        return asyncio.run(coro)

    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            if isinstance(timeout_seconds, float) and timeout_seconds > 0:
                async def _with_timeout() -> object:
                    return await asyncio.wait_for(coro, timeout=timeout_seconds)

                result_box["value"] = asyncio.run(_with_timeout())
            else:
                result_box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagate exact exception.
            error_box["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _stable_hex_id(value: str, *, size_bytes: int) -> str:
    digest = sha256(value.encode("utf-8")).digest()
    return digest[:size_bytes].hex()


def _normalize_span_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if len(candidate) != 16:
        return None
    if any(ch not in "0123456789abcdef" for ch in candidate):
        return None
    return candidate


def _runtime_resource_attributes() -> list[dict[str, object]]:
    return [
        _otlp_attr("host.name", socket.gethostname()),
        _otlp_attr("process.pid", os.getpid()),
        _otlp_attr("process.runtime.name", platform.python_implementation()),
        _otlp_attr("process.runtime.version", platform.python_version()),
        _otlp_attr("telemetry.sdk.name", "stream-kernel"),
        _otlp_attr("telemetry.sdk.language", "python"),
    ]


def _otlp_attr(key: str, value: object) -> dict[str, object]:
    return {
        "key": key,
        "value": _otlp_any_value(value),
    }


def _otlp_any_value(value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, (dict, list, tuple)):
        return {"stringValue": json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=_json_default)}
    return {"stringValue": str(value)}


def _trace_to_opentracing_span(record: "TraceRecord", *, bridge_name: str) -> dict[str, object]:
    logs: list[dict[str, object]] = []
    if record.error is not None:
        logs.append(
            {
                "event": "error",
                "error.type": record.error.type,
                "error.message": record.error.message,
                "error.where": record.error.where,
            }
        )
    return {
        "bridge": bridge_name,
        "trace_id": record.trace_id,
        "operation_name": record.step_name,
        "start_time_ms": int(record.t_enter.timestamp() * 1000),
        "finish_time_ms": int(record.t_exit.timestamp() * 1000),
        "tags": {
            "scenario": record.scenario,
            "step_index": record.step_index,
            "status": record.status,
            "span_id": record.span_id,
            "parent_span_id": record.parent_span_id,
            "process_group": record.route.process_group if record.route is not None else None,
            "handoff_from": record.route.handoff_from if record.route is not None else None,
            "route_hop": record.route.route_hop if record.route is not None else None,
        },
        "logs": logs,
    }


def _as_dict(obj: object) -> object:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _format_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(obj: object) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)
