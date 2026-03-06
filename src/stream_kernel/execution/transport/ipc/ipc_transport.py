from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.integration.kv_store import KVStore

EXECUTION_IPC_LANE_CONTROL = "control"
EXECUTION_IPC_LANE_DATA = "data"
EXECUTION_IPC_LANE_TRACE = "trace"
EXECUTION_IPC_LANE_LOG = "log"
EXECUTION_IPC_LANE_METRIC = "metric"

_EXECUTION_IPC_TARGET_LANE_SEPARATOR = "::"
_EXECUTION_IPC_SUPPORTED_LANES = {
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_TRACE,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
}


@dataclass(frozen=True, slots=True)
class ExecutionIpcAck:
    status: str
    enqueued: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionIpcMessage:
    target_id: str
    payload: object
    ts_epoch_ms: int


@dataclass(frozen=True, slots=True)
class ExecutionIpcReceivePolicy:
    buffer_enabled: bool = True
    batch_max_items: int = 64
    flush_interval_ms: int = 20


@dataclass(frozen=True, slots=True)
class ExecutionIpcControlSignal:
    kind: str
    count: int = 0


def normalize_execution_ipc_lane(lane: str | None) -> str:
    if not isinstance(lane, str) or not lane:
        return EXECUTION_IPC_LANE_CONTROL
    normalized = lane.strip().lower()
    if normalized in _EXECUTION_IPC_SUPPORTED_LANES:
        return normalized
    return EXECUTION_IPC_LANE_CONTROL


def compose_execution_ipc_worker_target_id(
    worker_id: str,
    *,
    lane: str = EXECUTION_IPC_LANE_CONTROL,
) -> str:
    if not isinstance(worker_id, str) or not worker_id:
        raise ValueError("compose_execution_ipc_worker_target_id requires non-empty worker_id")
    normalized_lane = normalize_execution_ipc_lane(lane)
    if normalized_lane == EXECUTION_IPC_LANE_CONTROL:
        return worker_id
    return f"{worker_id}{_EXECUTION_IPC_TARGET_LANE_SEPARATOR}{normalized_lane}"


def decompose_execution_ipc_worker_target_id(target_id: str) -> tuple[str, str] | None:
    if not isinstance(target_id, str) or not target_id:
        return None
    if _EXECUTION_IPC_TARGET_LANE_SEPARATOR not in target_id:
        return (target_id, EXECUTION_IPC_LANE_CONTROL)
    worker_id, _sep, lane = target_id.partition(_EXECUTION_IPC_TARGET_LANE_SEPARATOR)
    if not worker_id:
        return None
    return (worker_id, normalize_execution_ipc_lane(lane))


def execution_ipc_worker_lane_targets(worker_id: str) -> dict[str, str]:
    return {
        lane: compose_execution_ipc_worker_target_id(worker_id, lane=lane)
        for lane in (
            EXECUTION_IPC_LANE_CONTROL,
            EXECUTION_IPC_LANE_DATA,
            EXECUTION_IPC_LANE_TRACE,
            EXECUTION_IPC_LANE_LOG,
            EXECUTION_IPC_LANE_METRIC,
        )
    }


def resolve_execution_ipc_lane_for_target(target: str | None) -> str:
    if not isinstance(target, str) or not target:
        return EXECUTION_IPC_LANE_DATA
    lowered = target.strip().lower()
    if lowered.startswith("system.cp."):
        return EXECUTION_IPC_LANE_CONTROL
    if lowered.startswith("system.obs.trace"):
        return EXECUTION_IPC_LANE_TRACE
    if lowered.startswith("system.obs.log"):
        return EXECUTION_IPC_LANE_LOG
    if (
        lowered.startswith("system.obs.metric")
        or lowered.startswith("system.obs.monitor")
        or lowered.startswith("system.obs.worker_queue")
    ):
        return EXECUTION_IPC_LANE_METRIC
    return EXECUTION_IPC_LANE_DATA


def resolve_execution_ipc_target_id(qualifier: str | None) -> str | None:
    if qualifier is None:
        return None
    if qualifier == "control":
        return "control"
    return f"group:{qualifier}"


class ExecutionIpcPort:
    def __init__(
        self,
        *,
        service: ExecutionIpcTransportService,
        target_id: str | None = None,
    ) -> None:
        self._service = service
        self._target_id = target_id

    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> ExecutionIpcAck | None:
        return self._service.send(target_id, payload, no_reply=no_reply)

    def recv(self, timeout: float | None = None) -> ExecutionIpcMessage | None:
        if not isinstance(self._target_id, str) or not self._target_id:
            raise ValueError("ExecutionIpcPort.recv requires a bound target_id")
        return self._service.recv(self._target_id, timeout=timeout)

    def metrics(self) -> dict[str, object]:
        if not isinstance(self._target_id, str) or not self._target_id:
            raise ValueError("ExecutionIpcPort.metrics requires a bound target_id")
        return self._service.metrics(self._target_id)


class ExecutionIpcTransportService:
    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> ExecutionIpcAck | None:
        raise NotImplementedError("ExecutionIpcTransportService.send must be implemented")

    def recv(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        raise NotImplementedError("ExecutionIpcTransportService.recv must be implemented")

    def metrics(self, target_id: str) -> dict[str, object]:
        raise NotImplementedError("ExecutionIpcTransportService.metrics must be implemented")

    def flush_pending(self, target_id: str) -> int:
        # Optional reliability hook: flush outbound messages buffered before endpoint registration.
        raise NotImplementedError("ExecutionIpcTransportService.flush_pending must be implemented")

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort:
        raise NotImplementedError("ExecutionIpcTransportService.build_port must be implemented")

    def allocate_local_endpoints(self, target_id: str) -> tuple[object, object]:
        # Allocate parent/child local endpoints for target_id.
        raise NotImplementedError("ExecutionIpcTransportService.allocate_local_endpoints must be implemented")

    def bind_local_endpoint(self, target_id: str, endpoint: object) -> None:
        # Attach a process-local endpoint (for example, child-side pipe endpoint) to target_id.
        raise NotImplementedError("ExecutionIpcTransportService.bind_local_endpoint must be implemented")


class ExecutionIpcEndpointRegistry(KVStore):
    # KV marker contract for IPC endpoint registry.
    # Values are endpoint objects keyed by target_id.
    pass


@runtime_checkable
class ExecutionIpcKvStreamPort(Protocol):
    # kv_stream adapter contract for IPC transport backends (pipe, tcp, in-memory).
    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> ExecutionIpcAck | None: ...

    def recv(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None: ...

    def metrics(self, target_id: str) -> dict[str, object]: ...

    def flush_pending(self, target_id: str) -> int: ...

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort: ...
