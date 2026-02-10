from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.inject import inject
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import QueuePort
from stream_kernel.platform.services.context import ContextService
from stream_kernel.platform.services.observability import ObservabilityService
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class SyncRunner:
    # Synchronous execution engine.
    # Responsibilities:
    # - pull work items from QueuePort;
    # - resolve context metadata by trace_id via ContextService;
    # - invoke target node;
    # - route node outputs via RoutingPort;
    # - push downstream envelopes back to QueuePort.
    #
    # This runner does not own dependency lifecycle: services/ports are injected by framework DI.
    nodes: dict[str, object]
    # Queue/routing are framework-managed dependencies and must come from DI.
    work_queue: object = inject.queue(Envelope, qualifier="execution.cpu")
    router: object = inject.service(RoutingPort)
    # Resolved through DI (`inject.service(ContextService)` in ApplicationContext wiring phase).
    context_service: object = inject.service(ContextService)
    # Framework-level observability gateway (tracing/metrics/logging hooks).
    observability: object = inject.service(ObservabilityService)
    # Service/system nodes can request full metadata, regular nodes receive filtered view.
    full_context_nodes: set[str] = field(default_factory=set)

    def run(self) -> None:
        # Drain current queue until empty.
        # Determinism: each popped envelope is fully executed and routed before next pop.
        context_service = self._context_service()
        work_queue = self._work_queue()
        router = self._router()
        observability = self._observability()
        while True:
            item = work_queue.pop()
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
            # Observability hooks can keep per-node temporary state (timers, snapshots, counters).
            observer_state = observability.before_node(
                node_name=node_name,
                payload=envelope.payload,
                ctx=raw_ctx,
                trace_id=envelope.trace_id,
            )
            try:
                # Node contract is `(payload, ctx) -> iterable[output]`.
                outputs = list(node(envelope.payload, node_ctx))
            except Exception as exc:
                # Error path is explicitly observable for diagnostics and metrics.
                observability.on_node_error(
                    node_name=node_name,
                    payload=envelope.payload,
                    ctx=raw_ctx,
                    trace_id=envelope.trace_id,
                    error=exc,
                    state=observer_state,
                )
                raise
            # Success path callback after node output materialization.
            observability.after_node(
                node_name=node_name,
                payload=envelope.payload,
                ctx=raw_ctx,
                trace_id=envelope.trace_id,
                outputs=outputs,
                state=observer_state,
            )

            # Router translates outputs to concrete `(target_node, payload)` deliveries.
            # Envelope trace_id emitted by node output overrides current trace_id if present.
            for output in outputs:
                explicit_trace_id = output.trace_id if isinstance(output, Envelope) else None
                deliveries = router.route([output], source=node_name)
                downstream_trace_id = explicit_trace_id or envelope.trace_id
                for target_name, payload in deliveries:
                    work_queue.push(
                        Envelope(payload=payload, target=target_name, trace_id=downstream_trace_id)
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
        work_queue = self._work_queue()
        router = self._router()
        for index, payload in enumerate(inputs, start=1):
            if isinstance(payload, Envelope) and payload.target is not None:
                trace_id = payload.trace_id or self._trace_id(run_id=run_id, index=index)
                context_service.seed(
                    trace_id=trace_id,
                    payload=payload.payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                )
                work_queue.push(Envelope(payload=payload.payload, target=payload.target, trace_id=trace_id))
                self.run()
                continue

            trace_id = self._trace_id(run_id=run_id, index=index)
            context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=run_id,
                scenario_id=scenario_id,
            )
            deliveries = router.route([payload])
            for target_name, routed_payload in deliveries:
                work_queue.push(
                    Envelope(payload=routed_payload, target=target_name, trace_id=trace_id)
                )
            self.run()

    def on_run_end(self) -> None:
        # Finalize observability lifecycle once run loop is completed.
        self._observability().on_run_end()

    @staticmethod
    def _normalize(item: object) -> Envelope:
        # Internal queue contract: runner consumes only Envelope items.
        if isinstance(item, Envelope):
            return item
        raise ValueError("QueuePort must contain Envelope instances")

    def _context_service(self) -> ContextService:
        # Runtime guard: DI must resolve `inject.service(ContextService)` before execution starts.
        if not isinstance(self.context_service, ContextService):
            raise ValueError("SyncRunner context_service is not resolved via DI")
        return self.context_service

    def _work_queue(self) -> QueuePort:
        # Runtime guard: DI must resolve `inject.queue(Envelope, qualifier="execution.cpu")`.
        if isinstance(self.work_queue, QueuePort):
            return self.work_queue
        if callable(getattr(self.work_queue, "push", None)) and callable(
            getattr(self.work_queue, "pop", None)
        ):
            return self.work_queue  # type: ignore[return-value]
        raise ValueError("SyncRunner work_queue is not resolved via DI")

    def _router(self) -> RoutingPort:
        # Runtime guard: DI must resolve `inject.service(RoutingPort)`.
        if isinstance(self.router, RoutingPort):
            return self.router
        if callable(getattr(self.router, "route", None)):
            return self.router  # type: ignore[return-value]
        raise ValueError("SyncRunner router is not resolved via DI")

    def _observability(self) -> ObservabilityService:
        # Runtime guard: DI must resolve `inject.service(ObservabilityService)`.
        if isinstance(self.observability, ObservabilityService):
            return self.observability
        if (
            callable(getattr(self.observability, "before_node", None))
            and callable(getattr(self.observability, "after_node", None))
            and callable(getattr(self.observability, "on_node_error", None))
            and callable(getattr(self.observability, "on_run_end", None))
        ):
            return self.observability  # type: ignore[return-value]
        raise ValueError("SyncRunner observability is not resolved via DI")

    @staticmethod
    def _trace_id(*, run_id: str, index: int) -> str:
        # Stable trace id format for reproducible runs.
        return f"{run_id}:{index}"
