from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import multiprocessing as mp
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from stream_kernel.application_context.service import service
from stream_kernel.kernel.trace import MessageSignature, RouteInfo, TraceRecord
from stream_kernel.observability.adapters.tracing import (
    trace_jsonl,
    trace_opentracing_bridge,
    trace_otel_otlp,
    trace_stdout,
)
from stream_kernel.observability.adapters.logging import (
    JsonlLogSink,
    PlainFileLogSink,
    StdoutLogSink,
    StdoutPlainLogSink,
    resolve_log_output_path,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.platform.services.observability import (
    ObservabilityService as ObservabilityServiceContract,
)
from stream_kernel.platform.services.runtime.process_group_router import (
    InMemoryProcessGroupRouterService,
    ProcessGroupRouterService,
)
from stream_kernel.platform.services.runtime.async_dispatch_loop import AsyncDispatchLoop
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

    def configure_tracing(self, settings: dict[str, object], *, strict: bool = True) -> None:
        # Optional hook: configure supervisor-owned trace sink fanout for boundary transport diagnostics.
        _ = (settings, strict)
        return None

    def configure_dispatch_policy(self, settings: dict[str, object]) -> None:
        # Optional hook: configure boundary dispatch semantics (stream|batch).
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

    def wait_output_closed(self, timeout_seconds: int) -> bool:
        # Optional Phase-D hook: wait for child to signal that on_run_end() has completed
        # and all output files/sinks are flushed. Returns True if ack received within timeout.
        # Default: return True immediately (no-op for supervisors without explicit flush signalling).
        _ = timeout_seconds
        return True

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
    runner_profile_effective: str | None = None
    output_closed: bool = False


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

    async def emit_async(self, message: LogMessage) -> None:
        for sink in list(self.sinks):
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(message)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    continue
                continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    await asyncio.to_thread(emit, message)
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

    runtime_raw = getattr(bundle, "runtime")
    runtime_copy = copy.deepcopy(runtime_raw) if isinstance(runtime_raw, dict) else {}
    runtime_copy["__process_role"] = "worker"

    return ChildBootstrapBundle(
        scenario_id=getattr(bundle, "scenario_id"),
        process_group=group_name,
        discovery_modules=list(getattr(bundle, "discovery_modules")),
        runtime=runtime_copy,
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

    return list(
        execute_child_boundary_loop_with_runtime(
            child=child_runtime,
            inputs=inputs,
            finalize=False,
        )
    )


def _close_child_runtime(child_runtime: object | None) -> None:
    if child_runtime is None:
        return
    scope = getattr(child_runtime, "scenario_scope", None)
    resolve = getattr(scope, "resolve", None)
    if callable(resolve):
        try:
            observability = resolve("service", ObservabilityServiceContract)
            on_run_end = getattr(observability, "on_run_end", None)
            if callable(on_run_end):
                on_run_end()
        except Exception:
            pass
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


def _is_process_group_router(candidate: object) -> bool:
    return (
        callable(getattr(candidate, "configure_process_groups", None))
        and callable(getattr(candidate, "configure_routing_cache", None))
        and callable(getattr(candidate, "resolve_group_for_target", None))
        and callable(getattr(candidate, "snapshot", None))
    )


def _as_process_group_router(candidate: object) -> ProcessGroupRouterService:
    if _is_process_group_router(candidate):
        return candidate  # type: ignore[return-value]
    raise ValueError("MultiprocessBootstrapSupervisor process_group_router does not match ProcessGroupRouterService")


def _chunk_list(items: list[object], size: int) -> list[list[object]]:
    if size <= 0:
        return [list(items)]
    return [items[index:index + size] for index in range(0, len(items), size)]


def _build_supervisor_trace_sink(*, kind: str, settings: dict[str, object]) -> object:
    if kind == "jsonl":
        return trace_jsonl(settings)
    if kind == "stdout":
        return trace_stdout(settings)
    if kind in {"otel_otlp", "otel_otlp_logical", "otel_otlp_topology"}:
        return trace_otel_otlp(settings)
    if kind == "opentracing_bridge":
        return trace_opentracing_bridge(settings)
    raise ValueError(f"unsupported tracing exporter kind: {kind}")


def _is_trace_sink_like(candidate: object) -> bool:
    emit = getattr(candidate, "emit", None)
    flush = getattr(candidate, "flush", None)
    close = getattr(candidate, "close", None)
    return callable(emit) and callable(flush) and callable(close)


def _extract_trace_record(payload: object) -> TraceRecord | None:
    if isinstance(payload, TraceRecord):
        return payload
    if isinstance(payload, TraceDispatchEvent) and isinstance(payload.payload, TraceRecord):
        return payload.payload
    return None


def _build_supervisor_hop_record(
    *,
    trace_record: TraceRecord,
    source_group: str,
    route_hop: int,
    parent_span_id: str | None,
) -> TraceRecord:
    now = datetime.now(tz=UTC)
    return TraceRecord(
        trace_id=trace_record.trace_id,
        scenario=trace_record.scenario,
        step_index=-1,
        step_name="system.obs.supervisor_handoff",
        work_index=trace_record.work_index,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=MessageSignature(type_name="TraceDispatchEvent", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="TraceRecord", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
        route=RouteInfo(
            process_group="supervisor.transport",
            handoff_from=source_group,
            route_hop=route_hop,
            parent_span_id=parent_span_id,
            runner_gap_ms=None,
        ),
        span_id=None,
        parent_span_id=parent_span_id,
    )


def _coerce_trace_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _coerce_span_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _coerce_route_hop(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _as_type_name(value: object) -> str:
    return type(value).__name__


def _make_system_span_id(*, material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).digest()[:8].hex()


def _build_message_lifecycle_record(
    *,
    trace_id: str,
    scenario_id: str,
    step_name: str,
    msg_in_payload: object,
    msg_out_type_names: tuple[str, ...],
    process_group: str,
    handoff_from: str | None,
    route_hop: int | None,
    parent_span_id: str | None,
    context_details: dict[str, object] | None = None,
) -> TraceRecord:
    now = datetime.now(tz=UTC)
    span_material = (
        f"{trace_id}:{step_name}:{process_group}:{handoff_from or '-'}:"
        f"{route_hop if route_hop is not None else -1}:{parent_span_id or '-'}:"
        f"{','.join(msg_out_type_names) if msg_out_type_names else '-'}"
    )
    route = RouteInfo(
        process_group=process_group,
        handoff_from=handoff_from,
        route_hop=route_hop,
        parent_span_id=parent_span_id,
        runner_gap_ms=None,
    )
    return TraceRecord(
        trace_id=trace_id,
        scenario=scenario_id,
        step_index=-1,
        step_name=step_name,
        work_index=0,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=MessageSignature(type_name=_as_type_name(msg_in_payload), identity=None, hash=None),
        msg_out=tuple(
            MessageSignature(type_name=type_name, identity=None, hash=None)
            for type_name in msg_out_type_names
        ),
        msg_out_count=len(msg_out_type_names),
        ctx_before=None,
        ctx_after=None,
        ctx_diff=dict(context_details) if isinstance(context_details, dict) and context_details else None,
        status="ok",
        error=None,
        route=route,
        span_id=_make_system_span_id(material=span_material),
        parent_span_id=parent_span_id,
    )


_LIFECYCLE_LEVEL_ORDER = {
    "off": 0,
    "none": 0,
    "info": 1,
    "debug": 2,
    "full": 3,
}

_SUPERVISOR_EVENT_MIN_LEVEL: dict[str, str] = {
    "worker_spawned": "info",
    "worker_bootstrapped": "info",
    "worker_ready": "info",
    "worker_stopping": "info",
    "worker_output_closed": "info",
    "worker_stopped": "info",
    "supervisor_start_groups": "info",
    "route_cache_configured": "debug",
    "route_cache_invalidated": "debug",
    "worker_failed": "debug",
    "stop_event_unavailable": "debug",
    "control_channel_unavailable": "debug",
    "boundary_dispatch_started": "full",
    "boundary_dispatch_completed": "full",
}

_WORKER_MESSAGE_MIN_LEVEL: dict[str, str] = {
    "bootstrap.worker_loop_started": "info",
    "bootstrap.worker_bootstrapped": "info",
    "bootstrap.worker_stop_event_received": "info",
    "bootstrap.worker_stop_command": "info",
    "bootstrap.worker_bootstrap_failed": "debug",
    "bootstrap.worker_unsupported_command": "debug",
    "bootstrap.worker_execute_boundary_timeout": "debug",
    "bootstrap.worker_execute_boundary_transport_error": "debug",
    "bootstrap.worker_execute_boundary_error": "debug",
    "bootstrap.worker_execute_boundary_result": "full",
}
for _worker_message_name, _worker_message_level in _WORKER_MESSAGE_MIN_LEVEL.items():
    if _worker_message_name.startswith("bootstrap."):
        _SUPERVISOR_EVENT_MIN_LEVEL.setdefault(
            _worker_message_name.removeprefix("bootstrap."),
            _worker_message_level,
        )
_SUPPORTED_BOUNDARY_DISPATCH_MODES = {"stream", "batch"}


def _normalize_lifecycle_level(level: object) -> str:
    if isinstance(level, str) and level:
        normalized = level.lower()
        if normalized in _LIFECYCLE_LEVEL_ORDER:
            return normalized
    return "info"


def _lifecycle_allows(*, configured: str, required: str) -> bool:
    return _LIFECYCLE_LEVEL_ORDER.get(configured, 0) >= _LIFECYCLE_LEVEL_ORDER.get(required, 0)


def _supervisor_event_level(kind: str) -> str:
    return _SUPERVISOR_EVENT_MIN_LEVEL.get(kind, "debug")


def _resolve_lifecycle_log_sink(
    *,
    settings: dict[str, object] | None,
    exporter_mode: str = "lifecycle",
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
        mode_raw = exporter.get("mode", "lifecycle")
        mode = mode_raw if isinstance(mode_raw, str) and mode_raw else "lifecycle"
        if mode != exporter_mode:
            continue
        kind = exporter.get("kind")
        if kind == "stdout":
            sinks.append(StdoutLogSink())
            continue
        if kind == "stdout_plain":
            sinks.append(StdoutPlainLogSink())
            continue
        if kind not in {"jsonl", "file_plain"}:
            continue
        exporter_settings = exporter.get("settings", {})
        if not isinstance(exporter_settings, dict):
            continue
        ext = "jsonl" if kind == "jsonl" else "log"
        path_obj = resolve_log_output_path(
            exporter_settings,
            default_prefix="lifecycle",
            extension=ext,
        )
        flush_every_n = exporter_settings.get("flush_every_n", 1)
        if not isinstance(flush_every_n, int) or flush_every_n <= 0:
            flush_every_n = 1
        fsync_every_n = exporter_settings.get("fsync_every_n")
        if not isinstance(fsync_every_n, int) or fsync_every_n <= 0:
            fsync_every_n = None
        sinks.append(
            _build_lifecycle_file_sink(
                kind=kind,
                path=path_obj,
                flush_every_n=flush_every_n,
                fsync_every_n=fsync_every_n,
            )
        )
    if not sinks:
        return None
    if len(sinks) == 1:
        return sinks[0]
    return _FanoutLogSink(sinks=sinks)


def _build_lifecycle_file_sink(
    *,
    kind: object,
    path: Path,
    flush_every_n: int,
    fsync_every_n: int | None,
) -> object:
    if kind == "file_plain":
        return PlainFileLogSink(path, flush_every_n=flush_every_n, fsync_every_n=fsync_every_n)
    return JsonlLogSink(path, flush_every_n=flush_every_n, fsync_every_n=fsync_every_n)


def _emit_worker_lifecycle_event(
    control_pipe: object | None,
    *,
    message: str,
    fields: dict[str, object],
) -> None:
    _send_pipe_message(
        control_pipe,
        {
            "kind": "worker_lifecycle",
            "message": message,
            "fields": dict(fields),
        },
    )


def _emit_worker_trace_record(control_pipe: object | None, *, record: TraceRecord) -> None:
    _send_pipe_message(
        control_pipe,
        {
            "kind": "worker_trace",
            "record": record,
        },
    )


def _worker_loop(
    stop_event: object | None,
    control_child: object | None,
    child_bundle: object | None,
    worker_id: str | None = None,
    group_name: str | None = None,
    runner_profile: str | None = None,
    control_poll_seconds: float = 0.001,
) -> None:
    # Worker loop: accepts boundary execution commands and returns terminal outputs over control pipe.
    if not isinstance(control_poll_seconds, (int, float)) or control_poll_seconds <= 0:
        control_poll_seconds = 0.001

    base_fields = {
        "worker_id": worker_id,
        "process_name": worker_id or "worker",
        "group_name": group_name,
        "runner_profile": runner_profile or "auto",
        "pid": os.getpid(),
    }
    _emit_worker_lifecycle_event(
        control_child,
        message="bootstrap.worker_loop_started",
        fields=dict(base_fields),
    )

    child_runtime: object | None = None
    child_bootstrap_error: Exception | None = None
    if _is_child_bootstrap_bundle(child_bundle):
        try:
            child_runtime = _bootstrap_child_runtime(child_bundle)
            runner_profile_effective = getattr(child_runtime, "runner_profile_effective", runner_profile or "sync")
            runner_profile_nodes = getattr(child_runtime, "runner_profile_nodes", {})
            async_nodes = sorted(
                name
                for name, pool in runner_profile_nodes.items()
                if isinstance(name, str) and pool == "async"
            ) if isinstance(runner_profile_nodes, dict) else []
            raw_async_services = getattr(child_runtime, "async_service_contracts", [])
            raw_async_adapters = getattr(child_runtime, "async_adapter_bindings", [])
            async_services = (
                [item for item in raw_async_services if isinstance(item, str)]
                if isinstance(raw_async_services, list)
                else []
            )
            async_adapters = (
                [item for item in raw_async_adapters if isinstance(item, str)]
                if isinstance(raw_async_adapters, list)
                else []
            )
            raw_exporters = getattr(child_runtime, "observability_exporters", [])
            observability_exporters = (
                [item for item in raw_exporters if isinstance(item, str)]
                if isinstance(raw_exporters, list)
                else []
            )
            _send_pipe_message(
                control_child,
                {
                    "kind": "worker_bootstrapped",
                    "worker_id": worker_id,
                    "group_name": group_name,
                    "runner_profile_requested": runner_profile or "auto",
                    "runner_profile_effective": runner_profile_effective,
                    "async_nodes": async_nodes,
                    "async_services": async_services,
                    "async_adapters": async_adapters,
                    "observability_exporters": observability_exporters,
                    "node_runner_plan": runner_profile_nodes if isinstance(runner_profile_nodes, dict) else {},
                },
            )
        except Exception as exc:  # noqa: BLE001 - deterministic error payload is returned on execute requests.
            child_bootstrap_error = exc
            _emit_worker_lifecycle_event(
                control_child,
                message="bootstrap.worker_bootstrap_failed",
                fields={**base_fields, "error_type": type(exc).__name__},
            )

    is_set = getattr(stop_event, "is_set", None)
    while True:
        if callable(is_set) and bool(is_set()):
            _emit_worker_lifecycle_event(
                control_child,
                message="bootstrap.worker_stop_event_received",
                fields=dict(base_fields),
            )
            _close_child_runtime(child_runtime)
            _close_pipe(control_child)
            return

        poll = getattr(control_child, "poll", None)
        recv = getattr(control_child, "recv", None)
        send = getattr(control_child, "send", None)
        if not callable(poll) or not callable(recv) or not callable(send):
            time.sleep(control_poll_seconds)
            continue

        if not poll(control_poll_seconds):
            continue

        try:
            command = recv()
        except (EOFError, OSError):
            _close_child_runtime(child_runtime)
            _close_pipe(control_child)
            return

        if not isinstance(command, dict):
            continue

        kind = command.get("kind")
        correlation_id = command.get("correlation_id", "")

        if kind == "stop":
            _emit_worker_lifecycle_event(
                control_child,
                message="bootstrap.worker_stop_command",
                fields={**base_fields, "correlation_id": correlation_id},
            )
            _close_child_runtime(child_runtime)
            _send_pipe_message(
                control_child,
                {
                    "kind": "stop_ack",
                    "correlation_id": correlation_id,
                    "output_closed": True,
                },
            )
            _close_pipe(control_child)
            return

        if kind != "execute_boundary":
            _emit_worker_lifecycle_event(
                control_child,
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
            worker_receive_spans: dict[str, str] = {}
            worker_route_hops: dict[str, int | None] = {}
            for item in inputs_raw:
                trace_id = _coerce_trace_id(getattr(item, "trace_id", None))
                if trace_id is None:
                    continue
                route_hop = _coerce_route_hop(getattr(item, "route_hop", None))
                parent_span_id = _coerce_span_id(getattr(item, "span_id", None))
                source_group = getattr(item, "source_group", None)
                handoff_from = source_group if isinstance(source_group, str) and source_group else "supervisor.transport"
                target_name = getattr(item, "target", None)
                target_group = getattr(item, "dispatch_group", None)
                receive_record = _build_message_lifecycle_record(
                    trace_id=trace_id,
                    scenario_id=str(command.get("scenario_id", "scenario")),
                    step_name="system.obs.worker_boundary_receive",
                    msg_in_payload=getattr(item, "payload", item),
                    msg_out_type_names=("boundary_dispatch",),
                    process_group=group_name or "worker",
                    handoff_from=handoff_from,
                    route_hop=route_hop,
                    parent_span_id=parent_span_id,
                    context_details={
                        "worker_id": worker_id,
                        "target": target_name,
                        "dispatch_group": target_group,
                    },
                )
                worker_receive_spans[trace_id] = receive_record.span_id or parent_span_id or ""
                worker_route_hops[trace_id] = route_hop
                _emit_worker_trace_record(control_child, record=receive_record)
            terminal_outputs = _execute_child_boundary_from_runtime(
                child_runtime=child_runtime,
                inputs=list(inputs_raw),
            )
            for output in terminal_outputs:
                if not isinstance(output, Envelope):
                    continue
                trace_id = _coerce_trace_id(output.trace_id)
                if trace_id is None:
                    continue
                inbound_hop = worker_route_hops.get(trace_id)
                outbound_hop = (
                    (inbound_hop + 1)
                    if isinstance(inbound_hop, int) and inbound_hop >= 0
                    else None
                )
                parent_for_emit = worker_receive_spans.get(trace_id) or _coerce_span_id(output.span_id)
                emit_record = _build_message_lifecycle_record(
                    trace_id=trace_id,
                    scenario_id=str(command.get("scenario_id", "scenario")),
                    step_name="system.obs.worker_boundary_emit",
                    msg_in_payload=output.payload,
                    msg_out_type_names=("supervisor.transport",),
                    process_group=group_name or "worker",
                    handoff_from=group_name if isinstance(group_name, str) and group_name else None,
                    route_hop=outbound_hop,
                    parent_span_id=parent_for_emit,
                    context_details={
                        "worker_id": worker_id,
                        "target": output.target,
                    },
                )
                _emit_worker_trace_record(control_child, record=emit_record)
            _emit_worker_lifecycle_event(
                control_child,
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
            _emit_worker_lifecycle_event(
                control_child,
                message="bootstrap.worker_execute_boundary_timeout",
                fields=dict(base_fields),
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
            _emit_worker_lifecycle_event(
                control_child,
                message="bootstrap.worker_execute_boundary_transport_error",
                fields=dict(base_fields),
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
            _emit_worker_lifecycle_event(
                control_child,
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
        self._group_runner_profiles: dict[str, str] = {}
        self._group_nodes: dict[str, list[str]] = {}
        self._workers: dict[str, list[_WorkerHandle]] = {}
        self._events: list[dict[str, object]] = []
        self._lock = Lock()
        self._ready_after_seconds = 0.02
        self._boundary_timeout_seconds = 10.0
        self._bootstrap_channel: object | None = None
        self._child_bundle: object | None = None
        self._group_rr_cursor: dict[str, int] = {}
        self._lifecycle_logging_enabled = False
        self._lifecycle_log_level = "info"
        self._lifecycle_log_sink: object | None = None
        self._all_log_sink: object | None = None
        self._all_logging_enabled = False
        self._lifecycle_logging_settings: dict[str, object] = {}
        self._boundary_dispatch_mode = "stream"
        self._boundary_batch_max_items = 1000
        self._boundary_control_poll_seconds = 0.001
        self._trace_sinks: list[object] = []
        self._tracing_enabled = False
        self._trace_dispatch_loop: AsyncDispatchLoop[TraceRecord] | None = None
        self._trace_dispatch_dropped = 0
        self._last_output_closed = True
        self.process_group_router: ProcessGroupRouterService = InMemoryProcessGroupRouterService()

    def load_bootstrap_channel(self, channel: object) -> None:
        self._bootstrap_channel = channel

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        self._child_bundle = bundle

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        if not isinstance(settings, dict):
            raise ValueError("runtime.platform.routing_cache must be a mapping")
        router = _as_process_group_router(self.process_group_router)
        router.configure_routing_cache(settings)
        snapshot = router.snapshot()
        self._emit_event(
            kind="route_cache_configured",
            enabled=snapshot["enabled"],
            negative_cache=snapshot["negative_cache"],
            max_entries=snapshot["max_entries"],
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
        level = _normalize_lifecycle_level(level)
        exporters = settings.get("exporters", [])
        if not isinstance(exporters, list):
            raise ValueError("runtime.observability.logging.exporters must be a list when provided")

        sink = _resolve_lifecycle_log_sink(settings=settings, exporter_mode="lifecycle")
        all_sink = _resolve_lifecycle_log_sink(settings=settings, exporter_mode="all")

        effective_enabled = enabled and level not in {"off", "none"}

        if sink is None and effective_enabled:
            # Default dev profile: if lifecycle logging is enabled, stdout sink is used unless explicitly disabled.
            sink = StdoutLogSink()

        with self._lock:
            self._lifecycle_logging_enabled = effective_enabled
            self._lifecycle_log_level = level
            self._lifecycle_log_sink = sink
            self._all_log_sink = all_sink
            self._all_logging_enabled = all_sink is not None
            self._lifecycle_logging_settings = dict(settings)

    def configure_tracing(self, settings: dict[str, object], *, strict: bool = True) -> None:
        if not isinstance(settings, dict):
            settings = {}
        exporters = settings.get("exporters", [])
        if not isinstance(exporters, list):
            raise ValueError("runtime.observability.tracing.exporters must be a list when provided")
        sinks: list[object] = []
        for index, exporter in enumerate(exporters):
            if not isinstance(exporter, dict):
                continue
            if exporter.get("enabled") is False:
                continue
            kind = exporter.get("kind")
            if not isinstance(kind, str) or not kind:
                continue
            exporter_settings = exporter.get("settings", {})
            settings_for_build = dict(exporter_settings) if isinstance(exporter_settings, dict) else {}
            if kind in {"otel_otlp", "otel_otlp_logical", "otel_otlp_topology"}:
                backend = exporter.get("backend")
                if isinstance(backend, str) and backend and "backend" not in settings_for_build:
                    settings_for_build["backend"] = backend
                if kind == "otel_otlp_logical":
                    settings_for_build.setdefault("trace_view", "logical")
                    settings_for_build.setdefault("service_name_by_step", True)
                    settings_for_build.setdefault("service_name_by_process_group", False)
                    settings_for_build.setdefault("logical_include_platform_spans", False)
                    settings_for_build.setdefault("service_name_suffix", ".logical")
                    settings_for_build.setdefault("isolate_view_ids", True)
                elif kind == "otel_otlp_topology":
                    settings_for_build.setdefault("trace_view", "topology")
                    settings_for_build.setdefault("service_name_by_step", False)
                    settings_for_build.setdefault("service_name_by_process_group", True)
                    settings_for_build.setdefault("topology_include_business_spans", False)
                    settings_for_build.setdefault("service_name_suffix", ".topology")
                    settings_for_build.setdefault("isolate_view_ids", True)
            try:
                sink = _build_supervisor_trace_sink(kind=kind, settings=settings_for_build)
            except Exception:
                if strict:
                    raise ValueError(
                        "runtime.observability.tracing.exporters["
                        f"{index}] failed to build supervisor trace sink for kind '{kind}'"
                    )
                continue
            if _is_trace_sink_like(sink):
                sinks.append(sink)
        with self._lock:
            self._stop_trace_dispatch_loop_locked()
            self._trace_sinks = sinks
            self._tracing_enabled = bool(sinks)
            if self._tracing_enabled:
                self._start_trace_dispatch_loop_locked()

    def configure_dispatch_policy(self, settings: dict[str, object]) -> None:
        if not isinstance(settings, dict):
            raise ValueError("runtime.platform.boundary_dispatch must be a mapping when provided")
        mode = settings.get("mode", "stream")
        if not isinstance(mode, str) or not mode:
            raise ValueError("runtime.platform.boundary_dispatch.mode must be a non-empty string when provided")
        if mode not in _SUPPORTED_BOUNDARY_DISPATCH_MODES:
            raise ValueError(
                "runtime.platform.boundary_dispatch.mode must be one of: "
                f"{sorted(_SUPPORTED_BOUNDARY_DISPATCH_MODES)}"
            )
        batch_max_items = settings.get("batch_max_items", 1000)
        if not isinstance(batch_max_items, int) or batch_max_items <= 0:
            raise ValueError(
                "runtime.platform.boundary_dispatch.batch_max_items must be an integer > 0 when provided"
            )
        control_poll_ms = settings.get("control_poll_ms", 1.0)
        if not isinstance(control_poll_ms, (int, float)) or control_poll_ms <= 0:
            raise ValueError(
                "runtime.platform.boundary_dispatch.control_poll_ms must be a number > 0 when provided"
            )
        timeout_seconds = settings.get("timeout_seconds", 10.0)
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError(
                "runtime.platform.boundary_dispatch.timeout_seconds must be a number > 0 when provided"
            )
        with self._lock:
            self._boundary_dispatch_mode = mode
            self._boundary_batch_max_items = batch_max_items
            self._boundary_control_poll_seconds = float(control_poll_ms) / 1000.0
            self._boundary_timeout_seconds = float(timeout_seconds)

    def configure_process_groups(self, groups: list[dict[str, object]]) -> None:
        workers_map: dict[str, int] = {}
        runner_profiles_map: dict[str, str] = {}
        group_nodes_map: dict[str, list[str]] = {}
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
            raw_profile = group.get("runner_profile")
            if isinstance(raw_profile, str) and raw_profile:
                runner_profiles_map[name] = raw_profile
            else:
                runner_profiles_map[name] = "auto"
            nodes = group.get("nodes", [])
            if isinstance(nodes, list):
                group_nodes_map[name] = [
                    node_name
                    for node_name in nodes
                    if isinstance(node_name, str) and node_name
                ]
        router = _as_process_group_router(self.process_group_router)
        router.configure_process_groups(groups)
        with self._lock:
            self._group_workers = workers_map
            self._group_runner_profiles = runner_profiles_map
            self._group_nodes = group_nodes_map
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
                    runner_profile = self._group_runner_profiles.get(group_name, "auto")
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
                            runner_profile,
                            self._boundary_control_poll_seconds,
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
                        runner_profile=runner_profile,
                        group_nodes=self._group_nodes.get(group_name, []),
                        stop_strategy="event" if stop_event is not None else "terminate_fallback",
                        control_channel="pipe" if parent_pipe is not None else "unavailable",
                    )

            self._emit_event(
                kind="supervisor_start_groups",
                group_count=len(group_names),
                worker_count=sum(len(self._workers.get(name, [])) for name in group_names),
                runner_profiles={
                    name: self._group_runner_profiles.get(name, "auto")
                    for name in group_names
                },
                nodes_by_group={
                    name: self._group_nodes.get(name, [])
                    for name in group_names
                },
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
        # Drain pending worker bootstrap/control messages before first dispatch.
        # Without this, worker_bootstrapped events from non-target groups may appear
        # much later (only when the first command is sent to that group), which hides
        # real init timing during diagnostics.
        self._drain_all_worker_bootstrap_messages()
        mode = self._boundary_dispatch_mode
        if mode == "batch":
            return self._execute_boundary_batch(
                run_id=run_id,
                scenario_id=scenario_id,
                inputs=inputs,
            )
        return self._execute_boundary_stream(
            run_id=run_id,
            scenario_id=scenario_id,
            inputs=inputs,
        )

    def _execute_boundary_stream(
        self,
        *,
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        terminal_outputs: list[object] = []
        pending: deque[object] = deque(inputs)
        iterations = 0
        dispatched_total = 0
        outputs_total = 0
        requeued_total = 0
        terminal_total = 0
        max_iterations = max(10000, len(inputs) * 10000)
        self._emit_event(kind="boundary_dispatch_started", inputs=len(inputs), mode="stream")
        while pending:
            iterations += 1
            if iterations > max_iterations:
                raise RuntimeError("remote handoff failed: boundary dispatch recursion limit exceeded")

            item = pending.popleft()
            grouped = self._group_boundary_inputs([item])
            for group_name, group_inputs in grouped.items():
                for dispatched in group_inputs:
                    dispatched_total += 1
                    handle = self._select_worker_for_group(group_name)
                    if handle is None:
                        raise ConnectionError(f"remote handoff transport failed for group '{group_name}'")
                    self._emit_supervisor_dispatch_trace(
                        dispatched_item=dispatched,
                        dispatch_group=group_name,
                        scenario_id=scenario_id,
                    )
                    command = {
                        "kind": "execute_boundary",
                        "correlation_id": f"{run_id}:{scenario_id}:{group_name}:{time.time_ns()}",
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "inputs": [dispatched],
                    }
                    response = self._send_boundary_command(
                        handle,
                        command=command,
                        timeout_seconds=self._boundary_timeout_seconds,
                    )
                    output_count, requeued_count, terminal_count = self._consume_boundary_response(
                        response=response,
                        group_name=group_name,
                        scenario_id=scenario_id,
                        dispatched_item=dispatched,
                        pending=pending,
                        terminal_outputs=terminal_outputs,
                    )
                    outputs_total += output_count
                    requeued_total += requeued_count
                    terminal_total += terminal_count

        self._emit_event(
            kind="boundary_dispatch_completed",
            mode="stream",
            iterations=iterations,
            dispatched=dispatched_total,
            outputs=outputs_total,
            requeued=requeued_total,
            terminal=terminal_total,
        )
        self._flush_trace_sinks()
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=terminal_outputs)

    def _execute_boundary_batch(
        self,
        *,
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
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
        self._emit_event(kind="boundary_dispatch_started", inputs=len(pending), mode="batch")
        while pending:
            iterations += 1
            if iterations > max_iterations:
                raise RuntimeError("remote handoff failed: boundary dispatch recursion limit exceeded")

            grouped = self._group_boundary_inputs(pending)
            pending = []
            for group_name, group_inputs in grouped.items():
                for chunk in _chunk_list(group_inputs, self._boundary_batch_max_items):
                    dispatched_total += len(chunk)
                    handle = self._select_worker_for_group(group_name)
                    if handle is None:
                        raise ConnectionError(f"remote handoff transport failed for group '{group_name}'")
                    for dispatched in chunk:
                        self._emit_supervisor_dispatch_trace(
                            dispatched_item=dispatched,
                            dispatch_group=group_name,
                            scenario_id=scenario_id,
                        )
                    command = {
                        "kind": "execute_boundary",
                        "correlation_id": f"{run_id}:{scenario_id}:{group_name}:{time.time_ns()}",
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "inputs": list(chunk),
                    }
                    response = self._send_boundary_command(
                        handle,
                        command=command,
                        timeout_seconds=self._boundary_timeout_seconds,
                    )
                    output_count, requeued_count, terminal_count = self._consume_boundary_response(
                        response=response,
                        group_name=group_name,
                        scenario_id=scenario_id,
                        dispatched_item=chunk[-1],
                        pending=pending,
                        terminal_outputs=terminal_outputs,
                    )
                    outputs_total += output_count
                    requeued_total += requeued_count
                    terminal_total += terminal_count

        self._emit_event(
            kind="boundary_dispatch_completed",
            mode="batch",
            iterations=iterations,
            dispatched=dispatched_total,
            outputs=outputs_total,
            requeued=requeued_total,
            terminal=terminal_total,
        )
        self._flush_trace_sinks()
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=terminal_outputs)

    def _emit_supervisor_dispatch_trace(
        self,
        *,
        dispatched_item: object,
        dispatch_group: str,
        scenario_id: str,
    ) -> None:
        if not self._tracing_enabled:
            return
        trace_id = _coerce_trace_id(getattr(dispatched_item, "trace_id", None))
        if trace_id is None:
            return
        route_hop = _coerce_route_hop(getattr(dispatched_item, "route_hop", None))
        parent_span_id = _coerce_span_id(getattr(dispatched_item, "span_id", None))
        source_group_raw = getattr(dispatched_item, "source_group", None)
        source_group = (
            source_group_raw
            if isinstance(source_group_raw, str) and source_group_raw
            else "supervisor.entry"
        )
        target_name = getattr(dispatched_item, "target", None)
        record = _build_message_lifecycle_record(
            trace_id=trace_id,
            scenario_id=scenario_id,
            step_name="system.obs.supervisor_boundary_dispatch",
            msg_in_payload=getattr(dispatched_item, "payload", dispatched_item),
            msg_out_type_names=(f"dispatch_group:{dispatch_group}",),
            process_group="supervisor.transport",
            handoff_from=source_group,
            route_hop=route_hop,
            parent_span_id=parent_span_id,
            context_details={
                "dispatch_group": dispatch_group,
                "target": target_name,
            },
        )
        self._emit_trace_record(record)

    def _consume_boundary_response(
        self,
        *,
        response: dict[str, object],
        group_name: str,
        scenario_id: str,
        dispatched_item: object,
        pending: list[object] | deque[object],
        terminal_outputs: list[object],
    ) -> tuple[int, int, int]:
        response_kind = response.get("kind")
        if response_kind == "execute_boundary_result":
            outputs = response.get("terminal_outputs", [])
            if not isinstance(outputs, list):
                raise RuntimeError(f"remote handoff failed for group '{group_name}'")
            output_count = len(outputs)
            requeued_count = 0
            terminal_count = 0
            dispatched_hop = getattr(dispatched_item, "route_hop", None)
            hop_base = dispatched_hop if isinstance(dispatched_hop, int) and dispatched_hop >= 0 else 0
            for output in outputs:
                if not isinstance(output, Envelope):
                    raise RuntimeError(f"remote handoff failed for group '{group_name}'")
                if self._tracing_enabled:
                    trace_id = _coerce_trace_id(output.trace_id)
                    if trace_id is not None:
                        receive_record = _build_message_lifecycle_record(
                            trace_id=trace_id,
                            scenario_id=scenario_id,
                            step_name="system.obs.supervisor_boundary_receive",
                            msg_in_payload=output.payload,
                            msg_out_type_names=("boundary_route",),
                            process_group="supervisor.transport",
                            handoff_from=group_name,
                            route_hop=hop_base + 1,
                            parent_span_id=_coerce_span_id(output.span_id),
                            context_details={
                                "source_group": group_name,
                                "target": output.target,
                            },
                        )
                        self._emit_trace_record(receive_record)
                if self._consume_supervisor_trace_output(
                    output=output,
                    source_group=group_name,
                    route_hop=hop_base + 1,
                ):
                    continue
                dispatch_target = output.target if isinstance(output.target, str) and output.target else None
                if dispatch_target is None:
                    terminal_outputs.append(output)
                    terminal_count += 1
                    continue
                pending.append(
                    self._build_boundary_input_from_envelope(
                        output,
                        source_group=group_name,
                        route_hop=hop_base + 1,
                    )
                )
                requeued_count += 1
            return output_count, requeued_count, terminal_count

        category = response.get("category", "execution")
        message = response.get("message")
        detail = f": {message}" if isinstance(message, str) and message else ""
        if category == "timeout":
            raise TimeoutError(f"remote handoff timed out for group '{group_name}'{detail}")
        if category == "transport":
            raise ConnectionError(f"remote handoff transport failed for group '{group_name}'{detail}")
        raise RuntimeError(f"remote handoff failed for group '{group_name}'{detail}")

    def _consume_supervisor_trace_output(
        self,
        *,
        output: Envelope,
        source_group: str,
        route_hop: int,
    ) -> bool:
        if not self._tracing_enabled:
            return False
        trace_record = _extract_trace_record(output.payload)
        if not isinstance(trace_record, TraceRecord):
            return False
        trace_id = output.trace_id if isinstance(output.trace_id, str) and output.trace_id else trace_record.trace_id
        if trace_id and trace_id != trace_record.trace_id:
            trace_record = replace(trace_record, trace_id=trace_id)
        self._emit_trace_record(trace_record)
        self._emit_trace_record(
            _build_supervisor_hop_record(
                trace_record=trace_record,
                source_group=source_group,
                route_hop=route_hop,
                parent_span_id=trace_record.span_id or output.span_id,
            )
        )
        return True

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
                for handle in handles:
                    self._drain_worker_bootstrap_messages_locked(handle)
                elapsed = time.monotonic() - getattr(self, "_start_ts", 0.0)
                if elapsed >= self._ready_after_seconds:
                    for handle in handles:
                        if not handle.ready:
                            handle.ready = True
                            ready_fields: dict[str, object] = {
                                "group_name": handle.group_name,
                                "worker_id": handle.worker_id,
                                "pid": handle.process.pid,
                            }
                            if isinstance(handle.runner_profile_effective, str) and handle.runner_profile_effective:
                                ready_fields["runner_profile_effective"] = handle.runner_profile_effective
                            self._emit_event(
                                kind="worker_ready",
                                **ready_fields,
                            )
                    return True
            time.sleep(0.01)
        return False

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        with self._lock:
            self._last_output_closed = True
            deadline = time.monotonic() + max(1, graceful_timeout_seconds)
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
                    if drain_inflight:
                        remaining_for_stop = max(0.01, deadline - time.monotonic())
                        if self._try_send_stop_command(handle, timeout_seconds=remaining_for_stop):
                            self._emit_event(
                                kind="worker_output_closed",
                                group_name=handle.group_name,
                                worker_id=handle.worker_id,
                                pid=handle.process.pid,
                            )
                            continue
                        # Fallback when command channel is unavailable: stop-event/terminate.
                        self._last_output_closed = False
                        if callable(stop):
                            stop()
                            continue
                        if handle.process.is_alive():
                            handle.process.terminate()
                        continue

                    # Immediate stop path (non-draining): set event first, then best-effort command.
                    if callable(stop):
                        stop()
                        if self._try_send_stop_command(handle, timeout_seconds=0.1):
                            self._emit_event(
                                kind="worker_output_closed",
                                group_name=handle.group_name,
                                worker_id=handle.worker_id,
                                pid=handle.process.pid,
                            )
                    elif handle.process.is_alive():
                        # Fallback when stop-event primitives are unavailable in runtime environment.
                        self._last_output_closed = False
                        handle.process.terminate()

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
        self._close_trace_sinks()

    def wait_output_closed(self, timeout_seconds: int) -> bool:
        if timeout_seconds <= 0:
            return False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if not self._workers:
                    return bool(self._last_output_closed)
                handles = [handle for group in self._workers.values() for handle in group]
                if handles and all(handle.output_closed for handle in handles):
                    return True
            time.sleep(0.01)
        return False

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
        self._close_trace_sinks()

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
        return _as_process_group_router(self.process_group_router).snapshot()

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
        self._last_output_closed = False
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
        return _as_process_group_router(self.process_group_router).resolve_group_for_target(
            target=target,
            source_group=source_group,
        )

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

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if not poll(remaining):
                if raise_on_timeout:
                    raise TimeoutError(f"remote handoff timed out for group '{handle.group_name}'")
                return {"kind": "timeout"}

            try:
                response = recv()
            except Exception as exc:
                raise ConnectionError(f"remote handoff transport failed for group '{handle.group_name}'") from exc

            if self._handle_worker_bootstrap_message(handle, response):
                continue
            if isinstance(response, dict):
                return response
            raise RuntimeError(f"remote handoff failed for group '{handle.group_name}'")

    def _drain_worker_bootstrap_messages_locked(self, handle: _WorkerHandle) -> None:
        poll = getattr(handle.control_parent, "poll", None)
        recv = getattr(handle.control_parent, "recv", None)
        if not callable(poll) or not callable(recv):
            return
        while poll(0.0):
            try:
                message = recv()
            except Exception:
                return
            self._handle_worker_bootstrap_message(handle, message)

    def _drain_all_worker_bootstrap_messages(self) -> None:
        with self._lock:
            for handles in self._workers.values():
                for handle in handles:
                    self._drain_worker_bootstrap_messages_locked(handle)

    def _handle_worker_bootstrap_message(self, handle: _WorkerHandle, message: object) -> bool:
        if not isinstance(message, dict):
            return False
        message_kind = message.get("kind")
        if message_kind == "worker_lifecycle":
            worker_message = message.get("message")
            if not isinstance(worker_message, str) or not worker_message:
                return True
            event_kind = (
                worker_message.removeprefix("bootstrap.")
                if worker_message.startswith("bootstrap.")
                else worker_message
            )
            raw_fields = message.get("fields", {})
            event_fields = dict(raw_fields) if isinstance(raw_fields, dict) else {}
            event_fields.setdefault("group_name", handle.group_name)
            event_fields.setdefault("worker_id", handle.worker_id)
            event_fields.setdefault("pid", handle.process.pid)
            self._emit_event(kind=event_kind, **event_fields)
            return True
        if message_kind == "worker_trace":
            record = _extract_trace_record(message.get("record"))
            if isinstance(record, TraceRecord):
                self._emit_trace_record(record)
            return True
        if message_kind != "worker_bootstrapped":
            return False
        runner_profile_effective = message.get("runner_profile_effective")
        if isinstance(runner_profile_effective, str) and runner_profile_effective:
            handle.runner_profile_effective = runner_profile_effective
        self._emit_event(
            kind="worker_bootstrapped",
            group_name=handle.group_name,
            worker_id=handle.worker_id,
            pid=handle.process.pid,
            runner_profile_requested=message.get("runner_profile_requested", self._group_runner_profiles.get(handle.group_name, "auto")),
            runner_profile_effective=message.get("runner_profile_effective", self._group_runner_profiles.get(handle.group_name, "auto")),
            async_nodes=message.get("async_nodes", []),
            async_services=message.get("async_services", []),
            async_adapters=message.get("async_adapters", []),
            observability_exporters=message.get("observability_exporters", []),
            node_runner_plan=message.get("node_runner_plan", {}),
        )
        return True

    def _try_send_stop_command(self, handle: _WorkerHandle, *, timeout_seconds: float) -> bool:
        try:
            response = self._send_boundary_command(
                handle,
                command={
                    "kind": "stop",
                    "correlation_id": f"stop:{handle.worker_id}:{time.time_ns()}",
                },
                timeout_seconds=timeout_seconds,
                raise_on_timeout=False,
            )
        except Exception:
            handle.output_closed = False
            return False
        if not isinstance(response, dict) or response.get("kind") != "stop_ack":
            handle.output_closed = False
            return False
        handle.output_closed = bool(response.get("output_closed"))
        return handle.output_closed

    def _emit_event(self, *, kind: str, **fields: object) -> None:
        event_level = _supervisor_event_level(kind)
        event_fields = dict(fields)
        worker_pid = event_fields.pop("pid", None)
        if worker_pid is not None and "worker_pid" not in event_fields:
            event_fields["worker_pid"] = worker_pid
        event = {
            "kind": kind,
            "ts_epoch_ms": int(time.time() * 1000),
            "process_name": "supervisor",
            "pid": os.getpid(),
            **event_fields,
        }
        self._events.append(event)

        if self._all_logging_enabled:
            sink = self._all_log_sink
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(
                        LogMessage(
                            level=event_level,
                            message=f"bootstrap.{kind}",
                            timestamp=datetime.now(tz=UTC),
                            fields=dict(event),
                        )
                    )
                except Exception:
                    pass

        if not self._lifecycle_logging_enabled:
            return
        if not _lifecycle_allows(configured=self._lifecycle_log_level, required=event_level):
            return
        sink = self._lifecycle_log_sink
        emit = getattr(sink, "emit", None)
        if not callable(emit):
            return
        try:
            emit(
                LogMessage(
                    level=event_level,
                    message=f"bootstrap.{kind}",
                    timestamp=datetime.now(tz=UTC),
                    fields=dict(event),
                )
            )
        except Exception:
            return

    def _emit_trace_record(self, record: TraceRecord) -> None:
        with self._lock:
            loop = self._trace_dispatch_loop
        if loop is not None:
            submitted = loop.submit(record, timeout_seconds=0.2)
            if submitted:
                return
            with self._lock:
                self._trace_dispatch_dropped += 1
        self._emit_trace_record_inline(record)

    def _emit_trace_record_inline(self, record: TraceRecord) -> None:
        sinks = list(self._trace_sinks)
        for sink in sinks:
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(record)
                    if inspect.isawaitable(result):
                        loop = asyncio.new_event_loop()
                        try:
                            loop.run_until_complete(result)
                        finally:
                            loop.close()
                    continue
                except Exception:
                    continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(record)
                except Exception:
                    continue

    def _flush_trace_sinks(self) -> None:
        with self._lock:
            loop = self._trace_dispatch_loop
        if loop is not None:
            _ = loop.drain(timeout_seconds=2.0)
        for sink in list(self._trace_sinks):
            flush = getattr(sink, "flush", None)
            if callable(flush):
                try:
                    flush()
                except Exception:
                    continue

    def _close_trace_sinks(self) -> None:
        with self._lock:
            self._stop_trace_dispatch_loop_locked()
        for sink in list(self._trace_sinks):
            close = getattr(sink, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    continue
        self._trace_sinks = []
        self._tracing_enabled = False

    async def _emit_trace_record_async(self, record: TraceRecord) -> None:
        sinks = list(self._trace_sinks)
        for sink in sinks:
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(record)
                    if inspect.isawaitable(result):
                        await result
                    continue
                except Exception:
                    continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    await asyncio.to_thread(emit, record)
                except Exception:
                    continue

    def _start_trace_dispatch_loop_locked(self) -> None:
        loop = AsyncDispatchLoop[TraceRecord](
            name=f"trace.dispatch.supervisor.{os.getpid()}",
            handler=self._emit_trace_record_async,
            queue_max_items=8192,
        )
        loop.start()
        self._trace_dispatch_loop = loop

    def _stop_trace_dispatch_loop_locked(self) -> None:
        loop = self._trace_dispatch_loop
        if loop is None:
            return
        try:
            loop.stop(drain=True, timeout_seconds=2.0)
        except Exception:
            return
        finally:
            self._trace_dispatch_loop = None

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
