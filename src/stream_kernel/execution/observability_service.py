from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.observer import ExecutionObserver
from stream_kernel.platform.services.observability import ObservabilityService


@dataclass(slots=True)
class ObserverBackedObservabilityService(ObservabilityService):
    # Adapts legacy observer list to platform ObservabilityService contract.
    observers: list[ExecutionObserver] = field(default_factory=list)

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        return [
            observer.before_node(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
            )
            for observer in self.observers
        ]

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
        states = state if isinstance(state, list) else [None] * len(self.observers)
        for observer, observer_state in zip(self.observers, states, strict=False):
            observer.after_node(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                outputs=outputs,
                state=observer_state,
            )

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
        states = state if isinstance(state, list) else [None] * len(self.observers)
        for observer, observer_state in zip(self.observers, states, strict=False):
            observer.on_node_error(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                error=error,
                state=observer_state,
            )

    def on_run_end(self) -> None:
        for observer in self.observers:
            observer.on_run_end()

