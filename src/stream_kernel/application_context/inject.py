from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar, TYPE_CHECKING

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.integration.kv_store import validate_kv_contract_type

if TYPE_CHECKING:
    from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcReceivePolicy

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Injected:
    # Descriptor-like object representing a dependency to be injected.
    port_type: str
    data_type: type[Any]
    qualifier: str | None = None

    def resolve(self, scope: ScenarioScope) -> object:
        return scope.resolve(self.port_type, self.data_type, qualifier=self.qualifier)


@dataclass(frozen=True, slots=True)
class IpcInjected(Injected):
    receive_policy: "ExecutionIpcReceivePolicy | None" = None
    target_id: str | None = None

    def resolve(self, scope: ScenarioScope) -> object:
        if self.receive_policy is None:
            return super().resolve(scope)
        from stream_kernel.execution.transport.ipc.ipc_transport import (
            ExecutionIpcTransportService,
            resolve_execution_ipc_target_id,
        )

        service = scope.resolve("service", ExecutionIpcTransportService)
        target_id = self.target_id or resolve_execution_ipc_target_id(self.qualifier)
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionIpcPort receive_policy requires a target_id")
        return service.build_port(target_id=target_id, receive_policy=self.receive_policy)


class _InjectFactory:
    # Convenience helpers for standard framework ports.
    def stream(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="stream", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def kv_stream(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="kv_stream", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def kv(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        try:
            validate_kv_contract_type(data_type)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return Injected(port_type="kv", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def request(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="request", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def response(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="response", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def service(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="service", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def queue(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="queue", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def topic(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="topic", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def ipc(
        self,
        data_type: type[Any],
        *,
        qualifier: str | None = None,
        receive_policy: "ExecutionIpcReceivePolicy | None" = None,
    ) -> Injected:
        normalized = _normalize_qualifier(qualifier)
        if receive_policy is None:
            return Injected(port_type="ipc", data_type=data_type, qualifier=normalized)
        from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcReceivePolicy

        if not isinstance(receive_policy, ExecutionIpcReceivePolicy):
            raise ValueError("inject.ipc receive_policy must be an ExecutionIpcReceivePolicy")
        return IpcInjected(
            port_type="ipc",
            data_type=data_type,
            qualifier=normalized,
            receive_policy=receive_policy,
        )


def _normalize_qualifier(qualifier: str | None) -> str | None:
    if qualifier is None:
        return None
    if not isinstance(qualifier, str) or not qualifier:
        raise ValueError("inject qualifier must be a non-empty string when provided")
    return qualifier


inject = _InjectFactory()
