from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from collections.abc import Mapping

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.debug_buffer import (
    debug_instrument_service_methods,
)

from .ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    normalize_execution_ipc_lane,
    resolve_execution_ipc_lane_for_target,
)

_PAYLOAD_LANES_KEY = "execution.transport.ipc.lane_routing.payload_lanes"
_TARGET_PREFIX_LANES_KEY = "execution.transport.ipc.lane_routing.target_prefix_lanes"

_DEFAULT_TARGET_PREFIX_LANES: dict[str, str] = {
    "system.cp.": EXECUTION_IPC_LANE_CONTROL,
    "system.obs.trace": EXECUTION_IPC_LANE_TRACE,
    "system.obs.log": EXECUTION_IPC_LANE_LOG,
    "system.obs.debug": EXECUTION_IPC_LANE_LOG,
    "system.obs.metric": EXECUTION_IPC_LANE_METRIC,
    "system.obs.monitor": EXECUTION_IPC_LANE_METRIC,
    "system.obs.worker_queue": EXECUTION_IPC_LANE_METRIC,
}

_DEFAULT_PAYLOAD_LANES: dict[str, str] = {
    "stream_kernel.platform.services.runtime.control_plane_events.ControlPlaneLeafBoundaryExecuteCommand": EXECUTION_IPC_LANE_DATA,
    "stream_kernel.platform.services.runtime.control_plane_events.ControlPlaneLeafBoundaryResultEvent": EXECUTION_IPC_LANE_DATA,
    "stream_kernel.platform.services.runtime.control_plane_events.ControlPlaneLeafShutdownPrepareCommand": EXECUTION_IPC_LANE_CONTROL,
    "stream_kernel.platform.services.runtime.control_plane_events.ControlPlaneLeafDrainReadyEvent": EXECUTION_IPC_LANE_CONTROL,
    "stream_kernel.observability.events.TraceDispatchEvent": EXECUTION_IPC_LANE_TRACE,
    "stream_kernel.observability.events.LogDispatchEvent": EXECUTION_IPC_LANE_LOG,
    "stream_kernel.observability.events.DebugDispatchEvent": EXECUTION_IPC_LANE_LOG,
    "stream_kernel.observability.events.MetricDispatchEvent": EXECUTION_IPC_LANE_METRIC,
    "stream_kernel.observability.events.MonitorDispatchEvent": EXECUTION_IPC_LANE_METRIC,
    "stream_kernel.observability.events.MonitoringMetricsSnapshotEvent": EXECUTION_IPC_LANE_METRIC,
    "stream_kernel.observability.events.WorkerQueueTelemetryEvent": EXECUTION_IPC_LANE_METRIC,
}


class ExecutionIpcLaneRoutingStore(KVStore):
    # KV marker contract for IPC lane routing persistence.
    pass


@runtime_checkable
class ExecutionIpcLaneRoutingService(Protocol):
    def resolve_lane(
        self,
        *,
        target: str | None = None,
        payload: object | None = None,
        default_lane: str = EXECUTION_IPC_LANE_DATA,
    ) -> str:
        raise NotImplementedError

    def preload_snapshot(
        self,
        *,
        payload_type_lanes: Mapping[str, str] | None = None,
        target_prefix_lanes: Mapping[str, str] | None = None,
        replace: bool = False,
    ) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict[str, dict[str, str]]:
        raise NotImplementedError


@service(name="execution_ipc_lane_routing_service")
@debug_instrument_service_methods
@dataclass(slots=True)
class InMemoryExecutionIpcLaneRoutingService(ExecutionIpcLaneRoutingService):
    store: KVStore = inject.kv(ExecutionIpcLaneRoutingStore)
    runtime_debug_buffer: object | None = None

    def resolve_lane(
        self,
        *,
        target: str | None = None,
        payload: object | None = None,
        default_lane: str = EXECUTION_IPC_LANE_DATA,
    ) -> str:
        payload_lanes, target_prefix_lanes = self._snapshot_internal()
        payload_key = _payload_type_key(payload)
        if isinstance(payload_key, str) and payload_key:
            lane = payload_lanes.get(payload_key)
            if isinstance(lane, str) and lane:
                return normalize_execution_ipc_lane(lane)
        lowered_target = target.strip().lower() if isinstance(target, str) and target else None
        if isinstance(lowered_target, str):
            for prefix, lane in sorted(target_prefix_lanes.items(), key=lambda item: len(item[0]), reverse=True):
                if lowered_target.startswith(prefix):
                    return normalize_execution_ipc_lane(lane)
        fallback_lane = resolve_execution_ipc_lane_for_target(target)
        if isinstance(fallback_lane, str) and fallback_lane:
            return normalize_execution_ipc_lane(fallback_lane)
        return normalize_execution_ipc_lane(default_lane)

    def preload_snapshot(
        self,
        *,
        payload_type_lanes: Mapping[str, str] | None = None,
        target_prefix_lanes: Mapping[str, str] | None = None,
        replace: bool = False,
    ) -> int:
        current_payload, current_target = self._snapshot_internal()
        merged_payload = {} if replace else dict(current_payload)
        merged_target = {} if replace else dict(current_target)
        loaded = 0
        loaded += _merge_lane_map(merged_payload, payload_type_lanes)
        loaded += _merge_lane_map(merged_target, target_prefix_lanes, normalize_key=True)
        self.store.set(_PAYLOAD_LANES_KEY, merged_payload)
        self.store.set(_TARGET_PREFIX_LANES_KEY, merged_target)
        return loaded

    def snapshot(self) -> dict[str, dict[str, str]]:
        payload_lanes, target_prefix_lanes = self._snapshot_internal()
        return {
            "payload_type_lanes": dict(payload_lanes),
            "target_prefix_lanes": dict(target_prefix_lanes),
        }

    def _snapshot_internal(self) -> tuple[dict[str, str], dict[str, str]]:
        payload_lanes = _coerce_lane_map(self.store.get(_PAYLOAD_LANES_KEY))
        target_prefix_lanes = _coerce_lane_map(self.store.get(_TARGET_PREFIX_LANES_KEY), normalize_key=True)
        if not payload_lanes:
            payload_lanes = dict(_DEFAULT_PAYLOAD_LANES)
            self.store.set(_PAYLOAD_LANES_KEY, payload_lanes)
        if not target_prefix_lanes:
            target_prefix_lanes = dict(_DEFAULT_TARGET_PREFIX_LANES)
            self.store.set(_TARGET_PREFIX_LANES_KEY, target_prefix_lanes)
        return payload_lanes, target_prefix_lanes


def _payload_type_key(payload: object | None) -> str | None:
    if payload is None:
        return None
    token = payload if isinstance(payload, type) else payload.__class__
    module = getattr(token, "__module__", None)
    qualname = getattr(token, "__qualname__", None)
    if not isinstance(module, str) or not module:
        return None
    if not isinstance(qualname, str) or not qualname:
        return None
    return f"{module}.{qualname}"


def _coerce_lane_map(value: object, *, normalize_key: bool = False) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    mapped: dict[str, str] = {}
    for raw_key, raw_lane in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            continue
        if not isinstance(raw_lane, str) or not raw_lane:
            continue
        key = raw_key.strip().lower() if normalize_key else raw_key
        mapped[key] = normalize_execution_ipc_lane(raw_lane)
    return mapped


def _merge_lane_map(
    current: dict[str, str],
    incoming: Mapping[str, str] | None,
    *,
    normalize_key: bool = False,
) -> int:
    if not isinstance(incoming, Mapping):
        return 0
    loaded = 0
    for raw_key, raw_lane in incoming.items():
        if not isinstance(raw_key, str) or not raw_key:
            continue
        if not isinstance(raw_lane, str) or not raw_lane:
            continue
        key = raw_key.strip().lower() if normalize_key else raw_key
        current[key] = normalize_execution_ipc_lane(raw_lane)
        loaded += 1
    return loaded


__all__ = [
    "ExecutionIpcLaneRoutingStore",
    "ExecutionIpcLaneRoutingService",
    "InMemoryExecutionIpcLaneRoutingService",
]
