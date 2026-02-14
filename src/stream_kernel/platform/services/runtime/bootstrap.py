from __future__ import annotations

import multiprocessing as mp
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from stream_kernel.application_context.service import service
from stream_kernel.observability.adapters.logging import JsonlLogSink, StdoutLogSink
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult


class BootstrapSupervisor:
    # Bootstrap supervisor contract for process-group orchestration in process_supervisor mode.
    def load_bootstrap_channel(self, channel: object) -> None:
        # Optional Step-C hook for one-shot bootstrap key bundle distribution.
        _ = channel
        return None

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        # Optional Step-D hook for metadata-only child runtime bootstrap contract.
        _ = bundle
        return None

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        # Optional Step-Hook: configure outbound route cache policy for boundary dispatch.
        _ = settings
        return None

    def configure_lifecycle_logging(self, settings: dict[str, object]) -> None:
        # Optional hook: configure structured lifecycle logging emitted by supervisor.
        _ = settings
        return None

    def start_groups(self, group_names: list[str]) -> None:
        raise NotImplementedError("BootstrapSupervisor.start_groups must be implemented")

    def wait_ready(self, timeout_seconds: int) -> bool:
        raise NotImplementedError("BootstrapSupervisor.wait_ready must be implemented")

    def execute_boundary(
        self,
        *,
        run: Callable[[], None],
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        # Optional Step-D/E hook: execute workload through process boundary and return structured result.
        _ = (run, run_id, scenario_id, inputs)
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        raise NotImplementedError("BootstrapSupervisor.stop_groups must be implemented")

    def force_terminate_groups(self, group_names: list[str]) -> None:
        # Optional Step-E hook used when graceful stop timeout occurs.
        _ = group_names
        return None

    def emit_stop_event(self, *, group_name: str, mode: str) -> None:
        # Optional Step-E hook for lifecycle stop telemetry.
        _ = (group_name, mode)
        return None

    def emit_handoff_failure(self, *, group_name: str, category: str) -> None:
        # Optional Step-E hook for sanitized remote-handoff diagnostics.
        _ = (group_name, category)
        return None

    def route_cache_snapshot(self) -> dict[str, object]:
        # Optional diagnostics hook exposing route-cache health counters.
        return {}


@dataclass(slots=True)
class _WorkerHandle:
    group_name: str
    worker_index: int
    worker_id: str
    process: mp.Process
    stop_event: object | None = None
    control_parent: object | None = None
    ready: bool = False


@dataclass(slots=True)
class _FanoutLogSink:
    sinks: list[object]

    def emit(self, message: LogMessage) -> None:
        for sink in list(self.sinks):
            emit = getattr(sink, "emit", None)
            if not callable(emit):
                continue
            try:
                emit(message)
            except Exception:
                continue

    def close(self) -> None:
        for sink in list(self.sinks):
            close = getattr(sink, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                continue


def _is_child_bootstrap_bundle(bundle: object | None) -> bool:
    try:
        from stream_kernel.execution.orchestration.child_bootstrap import ChildBootstrapBundle
    except Exception:
        return False
    return isinstance(bundle, ChildBootstrapBundle)


def _build_child_bundle_for_group(bundle: object | None, group_name: str) -> object | None:
    if not _is_child_bootstrap_bundle(bundle):
        return bundle
    from stream_kernel.execution.orchestration.child_bootstrap import ChildBootstrapBundle

    return ChildBootstrapBundle(
        scenario_id=getattr(bundle, "scenario_id"),
        process_group=group_name,
        discovery_modules=list(getattr(bundle, "discovery_modules")),
        runtime=dict(getattr(bundle, "runtime")),
        key_bundle=getattr(bundle, "key_bundle"),
        run_id=getattr(bundle, "run_id", "run"),
        adapters=dict(getattr(bundle, "adapters", {}) or {}),
        config=dict(getattr(bundle, "config", {}) or {}),
    )


def _execute_child_boundary_from_bundle(*, bundle: object, inputs: list[object]) -> list[object]:
    from stream_kernel.execution.orchestration.child_bootstrap import execute_child_boundary_loop_from_bundle

    return list(execute_child_boundary_loop_from_bundle(bundle=bundle, inputs=inputs))


def _bootstrap_child_runtime(bundle: object) -> object:
    from stream_kernel.execution.orchestration.child_bootstrap import bootstrap_child_runtime_from_bundle

    return bootstrap_child_runtime_from_bundle(bundle)


def _execute_child_boundary_from_runtime(*, child_runtime: object, inputs: list[object]) -> list[object]:
    from stream_kernel.execution.orchestration.child_bootstrap import execute_child_boundary_loop_with_runtime

    return list(execute_child_boundary_loop_with_runtime(child=child_runtime, inputs=inputs))


def _close_child_runtime(child_runtime: object | None) -> None:
    if child_runtime is None:
        return
    scope = getattr(child_runtime, "scenario_scope", None)
    close = getattr(scope, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _send_pipe_message(pipe: object | None, payload: dict[str, object]) -> None:
    send = getattr(pipe, "send", None)
    if not callable(send):
        return
    try:
        send(payload)
    except Exception:
        return


def _close_pipe(pipe: object | None) -> None:
    close = getattr(pipe, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _safe_worker_file_name(worker_id: str) -> str:
    return worker_id.replace("/", "_").replace("#", "_")


def _resolve_lifecycle_log_sink(
    *,
    settings: dict[str, object] | None,
    worker_id: str | None = None,
    group_name: str | None = None,
) -> object | None:
    if not isinstance(settings, dict):
        return None
    exporters = settings.get("exporters", [])
    if not isinstance(exporters, list):
        exporters = []
    sinks: list[object] = []
    for exporter in exporters:
        if not isinstance(exporter, dict):
            continue
        kind = exporter.get("kind")
        if kind == "stdout":
            sinks.append(StdoutLogSink())
            continue
        if kind != "jsonl":
            continue
        exporter_settings = exporter.get("settings", {})
        if not isinstance(exporter_settings, dict):
            continue
        path = exporter_settings.get("path")
        if not isinstance(path, str) or not path:
            continue
        if worker_id is None:
            sinks.append(JsonlLogSink(Path(path)))
            continue
        workers_dir = exporter_settings.get("workers_dir")
        if isinstance(workers_dir, str) and workers_dir:
            worker_path = Path(workers_dir) / f"{_safe_worker_file_name(worker_id)}.jsonl"
        else:
            group_suffix = (
                _safe_worker_file_name(group_name)
                if isinstance(group_name, str) and group_name
                else "group"
            )
            worker_path = Path(path).parent / "workers" / group_suffix / f"{_safe_worker_file_name(worker_id)}.jsonl"
        sinks.append(JsonlLogSink(worker_path))
    if not sinks:
        return None
    if len(sinks) == 1:
        return sinks[0]
    return _FanoutLogSink(sinks=sinks)


def _emit_lifecycle_log(
    sink: object | None,
    *,
    level: str,
    message: str,
    fields: dict[str, object],
) -> None:
    emit = getattr(sink, "emit", None)
    if not callable(emit):
        return
    try:
        emit(
            LogMessage(
                level=level,
                message=message,
                timestamp=datetime.now(tz=UTC),
                fields=dict(fields),
            )
        )
    except Exception:
        return


def _close_log_sink(sink: object | None) -> None:
    close = getattr(sink, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _worker_loop(
    stop_event: object | None,
    control_child: object | None,
    child_bundle: object | None,
    worker_id: str | None = None,
    group_name: str | None = None,
    logging_settings: dict[str, object] | None = None,
) -> None:
    # Worker loop: accepts boundary execution commands and returns terminal outputs over control pipe.
    log_sink = _resolve_lifecycle_log_sink(
        settings=logging_settings,
        worker_id=worker_id,
        group_name=group_name,
    )
    log_level = "info"
    if isinstance(logging_settings, dict):
        lifecycle = logging_settings.get("lifecycle_events", {})
        if isinstance(lifecycle, dict):
            maybe_level = lifecycle.get("level")
            if isinstance(maybe_level, str) and maybe_level:
                log_level = maybe_level
    base_fields = {
        "worker_id": worker_id,
        "group_name": group_name,
        "pid": os.getpid(),
    }
    _emit_lifecycle_log(
        log_sink,
        level=log_level,
        message="bootstrap.worker_loop_started",
        fields=base_fields,
    )

    child_runtime: object | None = None
    child_bootstrap_error: Exception | None = None
    if _is_child_bootstrap_bundle(child_bundle):
        try:
            child_runtime = _bootstrap_child_runtime(child_bundle)
            _emit_lifecycle_log(
                log_sink,
                level=log_level,
                message="bootstrap.worker_bootstrapped",
                fields=base_fields,
            )
        except Exception as exc:  # noqa: BLE001 - deterministic error payload is returned on execute requests.
            child_bootstrap_error = exc
            _emit_lifecycle_log(
                log_sink,
                level="debug",
                message="bootstrap.worker_bootstrap_failed",
                fields={**base_fields, "error_type": type(exc).__name__},
            )

    is_set = getattr(stop_event, "is_set", None)
    while True:
        if callable(is_set) and bool(is_set()):
            _emit_lifecycle_log(
                log_sink,
                level=log_level,
                message="bootstrap.worker_stop_event_received",
                fields=base_fields,
            )
            _close_child_runtime(child_runtime)
            _close_pipe(control_child)
            _close_log_sink(log_sink)
            return

        poll = getattr(control_child, "poll", None)
        recv = getattr(control_child, "recv", None)
        send = getattr(control_child, "send", None)
        if not callable(poll) or not callable(recv) or not callable(send):
            time.sleep(0.05)
            continue

        if not poll(0.05):
            continue

        try:
            command = recv()
        except (EOFError, OSError):
            _close_child_runtime(child_runtime)
            _close_pipe(control_child)
            _close_log_sink(log_sink)
            return

        if not isinstance(command, dict):
            continue

        kind = command.get("kind")
        correlation_id = command.get("correlation_id", "")

        if kind == "stop":
            _emit_lifecycle_log(
                log_sink,
                level=log_level,
                message="bootstrap.worker_stop_command",
                fields={**base_fields, "correlation_id": correlation_id},
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "stop_ack",
                    "correlation_id": correlation_id,
                },
            )
            _close_child_runtime(child_runtime)
            _close_pipe(control_child)
            _close_log_sink(log_sink)
            return

        if kind != "execute_boundary":
            _emit_lifecycle_log(
                log_sink,
                level="debug",
                message="bootstrap.worker_unsupported_command",
                fields={**base_fields, "kind": kind},
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "transport",
                    "message": "unsupported control command",
                },
            )
            continue

        if not _is_child_bootstrap_bundle(child_bundle):
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "transport",
                    "message": "child bootstrap bundle is not loaded",
                },
            )
            continue

        if child_bootstrap_error is not None:
            detail = f"{type(child_bootstrap_error).__name__}: {child_bootstrap_error}"
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "execution",
                    "message": f"child bootstrap failed: {detail}",
                },
            )
            continue

        if child_runtime is None:
            try:
                child_runtime = _bootstrap_child_runtime(child_bundle)
            except Exception as exc:  # noqa: BLE001 - deterministic error payload is returned on execute requests.
                child_bootstrap_error = exc
                detail = f"{type(exc).__name__}: {exc}"
                _send_pipe_message(
                    control_child,
                    {
                        "kind": "execute_boundary_error",
                        "correlation_id": correlation_id,
                        "category": "execution",
                        "message": f"child bootstrap failed: {detail}",
                    },
                )
                continue

        try:
            inputs_raw = command.get("inputs", [])
            if not isinstance(inputs_raw, list):
                raise ValueError("boundary inputs payload must be a list")
            terminal_outputs = _execute_child_boundary_from_runtime(
                child_runtime=child_runtime,
                inputs=list(inputs_raw),
            )
            _emit_lifecycle_log(
                log_sink,
                level=log_level,
                message="bootstrap.worker_execute_boundary_result",
                fields={**base_fields, "outputs": len(terminal_outputs)},
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_result",
                    "correlation_id": correlation_id,
                    "terminal_outputs": list(terminal_outputs),
                },
            )
        except TimeoutError:
            _emit_lifecycle_log(
                log_sink,
                level="debug",
                message="bootstrap.worker_execute_boundary_timeout",
                fields=base_fields,
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "timeout",
                    "message": "child boundary execution timed out",
                },
            )
        except ConnectionError:
            _emit_lifecycle_log(
                log_sink,
                level="debug",
                message="bootstrap.worker_execute_boundary_transport_error",
                fields=base_fields,
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "transport",
                    "message": "child boundary transport failed",
                },
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            _emit_lifecycle_log(
                log_sink,
                level="debug",
                message="bootstrap.worker_execute_boundary_error",
                fields={**base_fields, "error_type": type(exc).__name__},
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "execute_boundary_error",
                    "correlation_id": correlation_id,
                    "category": "execution",
                    "message": f"child boundary execution failed: {detail}",
                },
            )


@service(name="bootstrap_supervisor_multiprocess")
class MultiprocessBootstrapSupervisor(BootstrapSupervisor):
    # Spawn-based supervisor baseline for process-group orchestration contracts.
    def __init__(self) -> None:
        self._ctx = mp.get_context("spawn")
        self._event_factory = self._ctx.Event
        self._group_workers: dict[str, int] = {}
        self._target_group_map: dict[str, str] = {}
        self._workers: dict[str, list[_WorkerHandle]] = {}
        self._events: list[dict[str, object]] = []
        self._lock = Lock()
        self._ready_after_seconds = 0.02
        self._boundary_timeout_seconds = 10.0
        self._bootstrap_channel: object | None = None
        self._child_bundle: object | None = None
        self._group_rr_cursor: dict[str, int] = {}
        self._route_cache_enabled = True
        self._route_cache_negative = True
        self._route_cache_max_entries = 100000
        self._route_cache: dict[tuple[str, str | None], str] = {}
        self._route_negative_cache: set[tuple[str, str | None]] = set()
        self._route_cache_hits = 0
        self._route_cache_misses = 0
        self._route_cache_negative_hits = 0
        self._route_cache_generation = 0
        self._lifecycle_logging_enabled = False
        self._lifecycle_log_level = "info"
        self._lifecycle_log_sink: object | None = None
        self._lifecycle_logging_settings: dict[str, object] = {}

    def load_bootstrap_channel(self, channel: object) -> None:
        self._bootstrap_channel = channel

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        self._child_bundle = bundle

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        if not isinstance(settings, dict):
            raise ValueError("runtime.platform.routing_cache must be a mapping")
        enabled = settings.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("runtime.platform.routing_cache.enabled must be a boolean when provided")
        negative_cache = settings.get("negative_cache", True)
        if not isinstance(negative_cache, bool):
            raise ValueError("runtime.platform.routing_cache.negative_cache must be a boolean when provided")
        max_entries = settings.get("max_entries", 100000)
        if not isinstance(max_entries, int):
            raise ValueError("runtime.platform.routing_cache.max_entries must be an integer when provided")
        if max_entries <= 0:
            raise ValueError("runtime.platform.routing_cache.max_entries must be > 0")
        with self._lock:
            self._route_cache_enabled = enabled
            self._route_cache_negative = negative_cache
            self._route_cache_max_entries = max_entries
            self._clear_route_cache_locked()
            self._emit_event(
                kind="route_cache_configured",
                enabled=enabled,
                negative_cache=negative_cache,
                max_entries=max_entries,
            )

    def configure_lifecycle_logging(self, settings: dict[str, object]) -> None:
        if not isinstance(settings, dict):
            settings = {}
        lifecycle = settings.get("lifecycle_events", {})
        if lifecycle is None:
            lifecycle = {}
        if not isinstance(lifecycle, dict):
            raise ValueError("runtime.observability.logging.lifecycle_events must be a mapping when provided")
        enabled = lifecycle.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("runtime.observability.logging.lifecycle_events.enabled must be a boolean when provided")
        level = lifecycle.get("level", "info")
        if not isinstance(level, str) or not level:
            raise ValueError("runtime.observability.logging.lifecycle_events.level must be a non-empty string")
        exporters = settings.get("exporters", [])
        if not isinstance(exporters, list):
            raise ValueError("runtime.observability.logging.exporters must be a list when provided")

        sink = _resolve_lifecycle_log_sink(settings=settings)

        if sink is None and enabled:
            # Default dev profile: if lifecycle logging is enabled, stdout sink is used unless explicitly disabled.
            sink = StdoutLogSink()

        with self._lock:
            self._lifecycle_logging_enabled = enabled
            self._lifecycle_log_level = level
            self._lifecycle_log_sink = sink
            self._lifecycle_logging_settings = dict(settings)

    def configure_process_groups(self, groups: list[dict[str, object]]) -> None:
        workers_map: dict[str, int] = {}
        target_map: dict[str, str] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            name = group.get("name")
            if not isinstance(name, str) or not name:
                continue
            workers = group.get("workers", 1)
            if not isinstance(workers, int) or workers <= 0:
                continue
            workers_map[name] = workers
            nodes = group.get("nodes", [])
            if isinstance(nodes, list):
                for node_name in nodes:
                    if not isinstance(node_name, str) or not node_name:
                        continue
                    existing = target_map.get(node_name)
                    if isinstance(existing, str) and existing != name:
                        raise ValueError(
                            f"runtime.platform.process_groups has duplicate placement for node '{node_name}'"
                        )
                    target_map[node_name] = name
        with self._lock:
            self._group_workers = workers_map
            self._target_group_map = target_map
            self._clear_route_cache_locked()
            self._emit_event(kind="route_cache_invalidated", reason="placement_update")

    def start_groups(self, group_names: list[str]) -> None:
        start_ts = time.monotonic()
        with self._lock:
            for group_name in group_names:
                workers_count = self._group_workers.get(group_name, 1)
                handles = self._workers.setdefault(group_name, [])
                self._group_rr_cursor[group_name] = 0

                # If a group is restarted, stop previous workers first.
                for handle in list(handles):
                    self._terminate_handle(handle, mode="forced")
                handles.clear()

                for index in range(workers_count):
                    worker_id = f"{group_name}#{index + 1}"
                    stop_event = self._build_stop_event()
                    parent_pipe, child_pipe = self._build_control_pipe()
                    worker_bundle = _build_child_bundle_for_group(self._child_bundle, group_name)
                    process = self._ctx.Process(
                        target=_worker_loop,
                        args=(
                            stop_event,
                            child_pipe,
                            worker_bundle,
                            worker_id,
                            group_name,
                            dict(self._lifecycle_logging_settings),
                        ),
                        name=f"sk:{worker_id}",
                        daemon=True,
                    )
                    process.start()
                    _close_pipe(child_pipe)

                    handle = _WorkerHandle(
                        group_name=group_name,
                        worker_index=index + 1,
                        worker_id=worker_id,
                        process=process,
                        stop_event=stop_event,
                        control_parent=parent_pipe,
                        ready=False,
                    )
                    handles.append(handle)
                    self._emit_event(
                        kind="worker_spawned",
                        group_name=group_name,
                        worker_id=worker_id,
                        pid=process.pid,
                        stop_strategy="event" if stop_event is not None else "terminate_fallback",
                        control_channel="pipe" if parent_pipe is not None else "unavailable",
                    )

            self._emit_event(
                kind="supervisor_start_groups",
                group_count=len(group_names),
                worker_count=sum(len(self._workers.get(name, [])) for name in group_names),
            )
            self._start_ts = start_ts

    def execute_boundary(
        self,
        *,
        run: Callable[[], None],
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        _ = run
        if not inputs:
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        terminal_outputs: list[object] = []
        pending: list[object] = list(inputs)
        iterations = 0
        dispatched_total = 0
        outputs_total = 0
        requeued_total = 0
        terminal_total = 0
        # Boundary loops can be long-lived when ingress source nodes re-schedule themselves
        # (one control envelope per produced payload). Keep a high deterministic cap to
        # detect accidental infinite loops without breaking valid large-file workloads.
        max_iterations = max(10000, len(pending) * 10000)
        self._emit_event(kind="boundary_dispatch_started", inputs=len(pending))
        while pending:
            iterations += 1
            if iterations > max_iterations:
                raise RuntimeError("remote handoff failed: boundary dispatch recursion limit exceeded")

            grouped = self._group_boundary_inputs(pending)
            pending = []
            for group_name, group_inputs in grouped.items():
                dispatched_total += len(group_inputs)
                handle = self._select_worker_for_group(group_name)
                if handle is None:
                    raise ConnectionError(f"remote handoff transport failed for group '{group_name}'")
                trace_hops: dict[str, int] = {}
                for dispatched in group_inputs:
                    dispatched_trace = getattr(dispatched, "trace_id", None)
                    dispatched_hop = getattr(dispatched, "route_hop", None)
                    if (
                        isinstance(dispatched_trace, str)
                        and dispatched_trace
                        and isinstance(dispatched_hop, int)
                        and dispatched_hop >= 0
                    ):
                        trace_hops[dispatched_trace] = max(
                            dispatched_hop,
                            trace_hops.get(dispatched_trace, -1),
                        )

                command = {
                    "kind": "execute_boundary",
                    "correlation_id": f"{run_id}:{scenario_id}:{group_name}:{time.time_ns()}",
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "inputs": list(group_inputs),
                }
                response = self._send_boundary_command(
                    handle,
                    command=command,
                    timeout_seconds=self._boundary_timeout_seconds,
                )
                response_kind = response.get("kind")
                if response_kind == "execute_boundary_result":
                    outputs = response.get("terminal_outputs", [])
                    if not isinstance(outputs, list):
                        raise RuntimeError(f"remote handoff failed for group '{group_name}'")
                    outputs_total += len(outputs)
                    for output in outputs:
                        if not isinstance(output, Envelope):
                            raise RuntimeError(f"remote handoff failed for group '{group_name}'")
                        dispatch_target = output.target if isinstance(output.target, str) and output.target else None
                        if dispatch_target is None:
                            terminal_outputs.append(output)
                            terminal_total += 1
                            continue
                        hop_hint = 0
                        if isinstance(output.trace_id, str) and output.trace_id:
                            hop_hint = trace_hops.get(output.trace_id, 0) + 1
                        pending.append(
                            self._build_boundary_input_from_envelope(
                                output,
                                source_group=group_name,
                                route_hop=hop_hint,
                            )
                        )
                        requeued_total += 1
                    continue

                category = response.get("category", "execution")
                message = response.get("message")
                detail = f": {message}" if isinstance(message, str) and message else ""
                if category == "timeout":
                    raise TimeoutError(f"remote handoff timed out for group '{group_name}'{detail}")
                if category == "transport":
                    raise ConnectionError(f"remote handoff transport failed for group '{group_name}'{detail}")
                raise RuntimeError(f"remote handoff failed for group '{group_name}'{detail}")

        self._emit_event(
            kind="boundary_dispatch_completed",
            iterations=iterations,
            dispatched=dispatched_total,
            outputs=outputs_total,
            requeued=requeued_total,
            terminal=terminal_total,
        )
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=terminal_outputs)

    def wait_ready(self, timeout_seconds: int) -> bool:
        if timeout_seconds <= 0:
            return False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                handles = [handle for group in self._workers.values() for handle in group]
                if not handles:
                    return False
                if not all(handle.process.is_alive() for handle in handles):
                    for handle in handles:
                        if not handle.process.is_alive():
                            self._emit_event(
                                kind="worker_failed",
                                group_name=handle.group_name,
                                worker_id=handle.worker_id,
                                pid=handle.process.pid,
                            )
                    return False
                elapsed = time.monotonic() - getattr(self, "_start_ts", 0.0)
                if elapsed >= self._ready_after_seconds:
                    for handle in handles:
                        if not handle.ready:
                            handle.ready = True
                            self._emit_event(
                                kind="worker_ready",
                                group_name=handle.group_name,
                                worker_id=handle.worker_id,
                                pid=handle.process.pid,
                            )
                    return True
            time.sleep(0.01)
        return False

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = drain_inflight
        with self._lock:
            for handles in self._workers.values():
                for handle in handles:
                    self._emit_event(
                        kind="worker_stopping",
                        group_name=handle.group_name,
                        worker_id=handle.worker_id,
                        pid=handle.process.pid,
                        mode="graceful",
                    )
                    stop = getattr(handle.stop_event, "set", None)
                    if callable(stop):
                        stop()
                        try:
                            _ = self._send_boundary_command(
                                handle,
                                command={
                                    "kind": "stop",
                                    "correlation_id": f"stop:{handle.worker_id}:{time.time_ns()}",
                                },
                                timeout_seconds=0.1,
                                raise_on_timeout=False,
                            )
                        except Exception:
                            # Stop signaling is best effort; process join/terminate is source of truth.
                            pass
                    elif handle.process.is_alive():
                        # Fallback when stop-event primitives are unavailable in runtime environment.
                        handle.process.terminate()

            deadline = time.monotonic() + max(1, graceful_timeout_seconds)
            for handles in self._workers.values():
                for handle in handles:
                    remaining = max(0.01, deadline - time.monotonic())
                    handle.process.join(timeout=remaining)
                    if handle.process.is_alive():
                        raise TimeoutError(
                            f"worker '{handle.worker_id}' did not stop within graceful timeout"
                        )
                    self._emit_event(
                        kind="worker_stopped",
                        group_name=handle.group_name,
                        worker_id=handle.worker_id,
                        pid=handle.process.pid,
                        mode="graceful",
                    )
                    _close_pipe(handle.control_parent)

            self._workers.clear()
            self._group_rr_cursor.clear()

    def force_terminate_groups(self, group_names: list[str]) -> None:
        with self._lock:
            target_groups = set(group_names)
            for group_name, handles in list(self._workers.items()):
                if group_name not in target_groups:
                    continue
                for handle in list(handles):
                    self._terminate_handle(handle, mode="forced")
                self._workers[group_name] = []
                self._group_rr_cursor.pop(group_name, None)

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {
                group_name: [
                    {
                        "worker_id": handle.worker_id,
                        "pid": handle.process.pid,
                        "alive": bool(handle.process.is_alive()),
                        "ready": bool(handle.ready),
                        "os_pid": int(handle.process.pid or os.getpid()),
                        "has_stop_event": bool(handle.stop_event is not None),
                        "has_control_channel": bool(handle.control_parent is not None),
                    }
                    for handle in handles
                ]
                for group_name, handles in self._workers.items()
            }

    def lifecycle_events(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._events)

    def route_cache_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._route_cache_enabled,
                "negative_cache": self._route_cache_negative,
                "max_entries": self._route_cache_max_entries,
                "generation": self._route_cache_generation,
                "hits": self._route_cache_hits,
                "misses": self._route_cache_misses,
                "negative_hits": self._route_cache_negative_hits,
                "positive_entries": len(self._route_cache),
                "negative_entries": len(self._route_negative_cache),
            }

    def _terminate_handle(self, handle: _WorkerHandle, *, mode: str) -> None:
        self._emit_event(
            kind="worker_stopping",
            group_name=handle.group_name,
            worker_id=handle.worker_id,
            pid=handle.process.pid,
            mode=mode,
        )
        if handle.process.is_alive():
            if mode == "forced":
                killer = getattr(handle.process, "kill", None)
                if callable(killer):
                    killer()
                else:
                    handle.process.terminate()
            else:
                handle.process.terminate()
        handle.process.join(timeout=1.0)
        _close_pipe(handle.control_parent)
        self._emit_event(
            kind="worker_stopped",
            group_name=handle.group_name,
            worker_id=handle.worker_id,
            pid=handle.process.pid,
            mode=mode,
        )

    def _group_boundary_inputs(self, inputs: list[object]) -> dict[str, list[object]]:
        grouped: dict[str, list[object]] = {}
        for item in inputs:
            if isinstance(item, Envelope):
                target = item.target if isinstance(item.target, str) and item.target else None
                if target is None:
                    continue
                item = self._build_boundary_input_from_envelope(
                    item,
                    source_group=None,
                    route_hop=0,
                )

            dispatch_group = getattr(item, "dispatch_group", None)
            if not isinstance(dispatch_group, str) or not dispatch_group:
                raise ConnectionError("remote handoff transport failed for group '<unknown>'")
            target = getattr(item, "target", None)
            if not isinstance(target, str) or not target:
                raise RuntimeError(f"remote handoff failed for group '{dispatch_group}'")
            grouped.setdefault(dispatch_group, []).append(item)
        return grouped

    def _build_boundary_input_from_envelope(
        self,
        envelope: Envelope,
        *,
        source_group: str | None,
        route_hop: int | None,
    ) -> object:
        target = envelope.target if isinstance(envelope.target, str) and envelope.target else None
        if target is None:
            raise RuntimeError("remote handoff failed for group '<unknown>'")
        dispatch_group = self._resolve_group_for_target(target=target, source_group=source_group)
        return SimpleNamespace(
            payload=envelope.payload,
            dispatch_group=dispatch_group,
            target=target,
            trace_id=envelope.trace_id,
            reply_to=envelope.reply_to,
            source_group=source_group,
            route_hop=route_hop,
            span_id=envelope.span_id,
        )

    def _resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        cache_key = (target, source_group if isinstance(source_group, str) and source_group else None)
        with self._lock:
            if self._route_cache_enabled:
                cached_group = self._route_cache.get(cache_key)
                if isinstance(cached_group, str) and cached_group:
                    self._route_cache_hits += 1
                    return cached_group
                if cache_key in self._route_negative_cache:
                    self._route_cache_negative_hits += 1
                    raise ConnectionError(f"remote handoff transport failed for group '{target}'")

            self._route_cache_misses += 1
            mapped_group = self._target_group_map.get(target)
            if isinstance(mapped_group, str) and mapped_group:
                if self._route_cache_enabled:
                    self._route_cache[cache_key] = mapped_group
                    self._route_negative_cache.discard(cache_key)
                    self._evict_route_cache_locked()
                return mapped_group

            if self._target_group_map:
                if self._route_cache_enabled and self._route_cache_negative:
                    self._route_negative_cache.add(cache_key)
                    self._evict_route_cache_locked()
                raise ConnectionError(f"remote handoff transport failed for group '{target}'")

            if isinstance(source_group, str) and source_group:
                if self._route_cache_enabled:
                    self._route_cache[cache_key] = source_group
                    self._route_negative_cache.discard(cache_key)
                    self._evict_route_cache_locked()
                return source_group

            if self._route_cache_enabled and self._route_cache_negative:
                self._route_negative_cache.add(cache_key)
                self._evict_route_cache_locked()
            raise ConnectionError(f"remote handoff transport failed for group '{target}'")

    def _clear_route_cache_locked(self) -> None:
        self._route_cache.clear()
        self._route_negative_cache.clear()
        self._route_cache_generation += 1

    def _evict_route_cache_locked(self) -> None:
        while len(self._route_cache) > self._route_cache_max_entries:
            oldest_key = next(iter(self._route_cache))
            self._route_cache.pop(oldest_key, None)
        while len(self._route_negative_cache) > self._route_cache_max_entries:
            self._route_negative_cache.pop()

    def _select_worker_for_group(self, group_name: str) -> _WorkerHandle | None:
        with self._lock:
            handles = self._workers.get(group_name, [])
            alive = [handle for handle in handles if handle.process.is_alive()]
            if not alive:
                return None
            cursor = self._group_rr_cursor.get(group_name, 0)
            selected = alive[cursor % len(alive)]
            self._group_rr_cursor[group_name] = (cursor + 1) % len(alive)
            return selected

    def _send_boundary_command(
        self,
        handle: _WorkerHandle,
        *,
        command: dict[str, object],
        timeout_seconds: float,
        raise_on_timeout: bool = True,
    ) -> dict[str, object]:
        send = getattr(handle.control_parent, "send", None)
        poll = getattr(handle.control_parent, "poll", None)
        recv = getattr(handle.control_parent, "recv", None)
        if not callable(send) or not callable(poll) or not callable(recv):
            raise ConnectionError(f"remote handoff transport failed for group '{handle.group_name}'")

        try:
            send(command)
        except Exception as exc:
            raise ConnectionError(f"remote handoff transport failed for group '{handle.group_name}'") from exc

        if not poll(max(0.0, timeout_seconds)):
            if raise_on_timeout:
                raise TimeoutError(f"remote handoff timed out for group '{handle.group_name}'")
            return {"kind": "timeout"}

        try:
            response = recv()
        except Exception as exc:
            raise ConnectionError(f"remote handoff transport failed for group '{handle.group_name}'") from exc

        if isinstance(response, dict):
            return response
        raise RuntimeError(f"remote handoff failed for group '{handle.group_name}'")

    def _emit_event(self, *, kind: str, **fields: object) -> None:
        event = {
            "kind": kind,
            "ts_epoch_ms": int(time.time() * 1000),
            **fields,
        }
        self._events.append(event)

        if not self._lifecycle_logging_enabled:
            return
        sink = self._lifecycle_log_sink
        emit = getattr(sink, "emit", None)
        if not callable(emit):
            return
        try:
            emit(
                LogMessage(
                    level=self._lifecycle_log_level,
                    message=f"bootstrap.{kind}",
                    timestamp=datetime.now(tz=UTC),
                    fields=dict(event),
                )
            )
        except Exception:
            return

    def _build_stop_event(self) -> object | None:
        # Primary path: stop-event based graceful signaling. Fallback: terminate-only when sem primitives unavailable.
        try:
            return self._event_factory()
        except (PermissionError, OSError, RuntimeError):
            self._emit_event(kind="stop_event_unavailable", fallback="terminate_fallback")
            return None

    def _build_control_pipe(self) -> tuple[object | None, object | None]:
        try:
            return self._ctx.Pipe(duplex=True)
        except (PermissionError, OSError, RuntimeError):
            self._emit_event(kind="control_channel_unavailable", fallback="pipe_missing")
            return (None, None)


@service(name="bootstrap_supervisor_local")
class LocalBootstrapSupervisor(BootstrapSupervisor):
    # In-process bootstrap baseline; starts requested groups in-place and reports immediate readiness.
    def __init__(self) -> None:
        self._started = False
        self._child_bundle: object | None = None

    def start_groups(self, group_names: list[str]) -> None:
        _ = group_names
        self._started = True

    def wait_ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        return self._started

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        self._child_bundle = bundle

    def execute_boundary(
        self,
        *,
        run: Callable[[], None],
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        # Local baseline keeps legacy behavior but can execute boundary batch via child bootstrap loop.
        _ = (run_id, scenario_id)
        if self._child_bundle is not None and inputs and _is_child_bootstrap_bundle(self._child_bundle):
            try:
                outputs = _execute_child_boundary_from_bundle(
                    bundle=self._child_bundle,
                    inputs=list(inputs),
                )
                return RoutingResult(
                    local_deliveries=[],
                    boundary_deliveries=[],
                    terminal_outputs=list(outputs),
                )
            except Exception:
                # Fallback to in-process callback path if child boundary bootstrap is not ready.
                pass
        run()
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = graceful_timeout_seconds
        _ = drain_inflight
        self._started = False

    def force_terminate_groups(self, group_names: list[str]) -> None:
        _ = group_names
        self._started = False
