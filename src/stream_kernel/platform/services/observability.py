from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service


@runtime_checkable
class ObservabilityService(Protocol):
    # Framework-level execution observability gateway.
    # Runner emits lifecycle events here; backend fan-out is implementation-defined.
    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        raise NotImplementedError("ObservabilityService.before_node must be implemented")

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> None:
        raise NotImplementedError("ObservabilityService.after_node must be implemented")

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        raise NotImplementedError("ObservabilityService.on_node_error must be implemented")

    def on_run_end(self) -> None:
        raise NotImplementedError("ObservabilityService.on_run_end must be implemented")


@service(name="observability_service")
@dataclass(slots=True)
class NoOpObservabilityService(ObservabilityService):
    # Default platform observability implementation when no concrete observers are configured.
    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        _ = (node_name, payload, ctx, trace_id)
        return None

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> None:
        _ = (node_name, payload, ctx, trace_id, outputs, state)
        return None

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        _ = (node_name, payload, ctx, trace_id, error, state)
        return None

    def on_run_end(self) -> None:
        return None

