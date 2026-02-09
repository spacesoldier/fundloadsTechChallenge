from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.inject import inject
from stream_kernel.execution.observer import ExecutionObserver
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import WorkQueue
from stream_kernel.platform.services.context import ContextService
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class SyncRunner:
    # Synchronous execution engine.
    # Responsibilities:
    # - pull work items from WorkQueue;
    # - resolve context metadata by trace_id via ContextService;
    # - invoke target node;
    # - route node outputs via RoutingPort;
    # - push downstream envelopes back to WorkQueue.
    #
    # This runner does not own dependency lifecycle: services/ports are injected by framework DI.
    nodes: dict[str, object]
    work_queue: WorkQueue
    routing_port: RoutingPort
    # Resolved through DI (`inject.service(ContextService)` in ApplicationContext wiring phase).
    context_service: object = inject.service(ContextService)
    # Optional hooks for tracing/telemetry/monitoring around node execution.
    observers: list[ExecutionObserver] = field(default_factory=list)
    # Service/system nodes can request full metadata, regular nodes receive filtered view.
    full_context_nodes: set[str] = field(default_factory=set)

    def run(self) -> None:
        # Drain current queue until empty.
        # Determinism: each popped envelope is fully executed and routed before next pop.
        context_service = self._context_service()
        while True:
            item = self.work_queue.pop()
            if item is None:
                break
            envelope = self._normalize(item)
            # Target must already be resolved by router. Runner executes, not decides topology.
            if envelope.target is None:
                raise ValueError("Envelope.target must be set before execution")
            target = envelope.target
            if isinstance(target, str):
                node_name = target
            else:
                raise ValueError("Envelope.target must resolve to a single node")
            if node_name not in self.nodes:
                raise ValueError(f"Unknown node '{node_name}'")

            # Context is loaded by trace_id. `full` grants internal keys for service/system nodes.
            raw_ctx = context_service.metadata(
                envelope.trace_id,
                full=(node_name in self.full_context_nodes),
            )
            # Pass a copy to the node so it cannot mutate persisted context in-place by accident.
            node_ctx = dict(raw_ctx)
            node = self.nodes[node_name]
            # Observers can keep per-node temporary state (timers, snapshots, counters).
            observer_states = [
                observer.before_node(
                    node_name=node_name,
                    payload=envelope.payload,
                    ctx=raw_ctx,
                    trace_id=envelope.trace_id,
                )
                for observer in self.observers
            ]
            try:
                # Node contract is `(payload, ctx) -> iterable[output]`.
                outputs = list(node(envelope.payload, node_ctx))
            except Exception as exc:
                # Error path is explicitly observable for diagnostics and metrics.
                for observer, state in zip(self.observers, observer_states, strict=False):
                    observer.on_node_error(
                        node_name=node_name,
                        payload=envelope.payload,
                        ctx=raw_ctx,
                        trace_id=envelope.trace_id,
                        error=exc,
                        state=state,
                    )
                raise
            # Success path callback after node output materialization.
            for observer, state in zip(self.observers, observer_states, strict=False):
                observer.after_node(
                    node_name=node_name,
                    payload=envelope.payload,
                    ctx=raw_ctx,
                    trace_id=envelope.trace_id,
                    outputs=outputs,
                    state=state,
                )

            # Router translates outputs to concrete `(target_node, payload)` deliveries.
            deliveries = self.routing_port.route(outputs, source=node_name)
            for target_name, payload in deliveries:
                # Downstream keeps same trace_id to preserve end-to-end correlation.
                self.work_queue.push(
                    Envelope(payload=payload, target=target_name, trace_id=envelope.trace_id)
                )

    def run_inputs(
        self,
        inputs: list[object],
        *,
        run_id: str,
        scenario_id: str,
    ) -> None:
        # Bootstrap entrypoint payloads.
        # For each input:
        # 1) allocate deterministic trace_id;
        # 2) seed context metadata;
        # 3) route initial payload to first consumers;
        # 4) drain queue end-to-end (`self.run()`).
        #
        # This preserves "message-by-message" deterministic processing.
        context_service = self._context_service()
        for index, payload in enumerate(inputs, start=1):
            trace_id = self._trace_id(run_id=run_id, index=index)
            context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=run_id,
                scenario_id=scenario_id,
            )
            deliveries = self.routing_port.route([payload])
            for target_name, routed_payload in deliveries:
                self.work_queue.push(
                    Envelope(payload=routed_payload, target=target_name, trace_id=trace_id)
                )
            self.run()

    @staticmethod
    def _normalize(item: object) -> Envelope:
        # Internal queue contract: runner consumes only Envelope items.
        if isinstance(item, Envelope):
            return item
        raise ValueError("WorkQueue must contain Envelope instances")

    def _context_service(self) -> ContextService:
        # Runtime guard: DI must resolve `inject.service(ContextService)` before execution starts.
        if not isinstance(self.context_service, ContextService):
            raise ValueError("SyncRunner context_service is not resolved via DI")
        return self.context_service

    @staticmethod
    def _trace_id(*, run_id: str, index: int) -> str:
        # Stable trace id format for reproducible runs.
        return f"{run_id}:{index}"
