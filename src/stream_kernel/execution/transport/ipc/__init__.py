from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ExecutionIpcCodec": "ipc_codec",
    "ExecutionIpcCodecError": "ipc_codec",
    "ExecutionIpcControlSignal": "ipc_transport",
    "ExecutionIpcEndpointRegistry": "ipc_transport",
    "ExecutionIpcKvStreamPort": "ipc_transport",
    "ExecutionIpcMessage": "ipc_transport",
    "ExecutionIpcPort": "ipc_transport",
    "ExecutionIpcReceivePolicy": "ipc_transport",
    "ExecutionIpcTransportService": "ipc_transport",
    "ExecutionIpcFlowControlPolicy": "flow_control",
    "NoopFlowControlPolicy": "flow_control",
    "CreditWindowFlowControlPolicy": "flow_control",
    "TokenBucketFlowControlPolicy": "flow_control",
    "HybridFlowControlPolicy": "flow_control",
    "resolve_execution_ipc_flow_control": "flow_control",
    "ExecutionIpcTransportCoordinatorService": "ipc_transport_service",
    "InMemoryExecutionIpcTransportAdapter": "carriers.ipc",
    "PipeExecutionIpcTransportAdapter": "carriers.ipc",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    mod_name = _EXPORTS.get(name)
    if mod_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if mod_name.startswith("carriers."):
        mod = import_module(f"stream_kernel.execution.transport.{mod_name}")
    else:
        mod = import_module(f"{__name__}.{mod_name}")
    value = getattr(mod, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))

