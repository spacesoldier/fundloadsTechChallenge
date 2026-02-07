from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.context_store import ContextStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import WorkQueue
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class SyncRunner:
    # Sync runner that executes nodes using a work queue (Execution runtime §6).
    nodes: dict[str, object]
    work_queue: WorkQueue
    context_store: ContextStore
    routing_port: RoutingPort

    def run(self) -> None:
        # Process messages until the queue is empty.
        while True:
            item = self.work_queue.pop()
            if item is None:
                break
            envelope = self._normalize(item)
            if envelope.target is None:
                # Messages without explicit target are invalid at execution time.
                raise ValueError("Envelope.target must be set before execution")
            target = envelope.target
            if isinstance(target, str):
                node_name = target
            else:
                # Execution-time envelopes should be resolved to a single target.
                raise ValueError("Envelope.target must resolve to a single node")
            if node_name not in self.nodes:
                raise ValueError(f"Unknown node '{node_name}'")

            ctx = self._resolve_context(envelope.trace_id)
            node = self.nodes[node_name]
            outputs = list(node(envelope.payload, ctx))

            # Route outputs and enqueue downstream work.
            deliveries = self.routing_port.route(outputs, source=node_name)
            for target_name, payload in deliveries:
                self.work_queue.push(
                    Envelope(payload=payload, target=target_name, trace_id=envelope.trace_id)
                )

    def _resolve_context(self, trace_id: str | None) -> dict[str, object]:
        # Resolve metadata view for the node (Execution runtime §3.3).
        if not trace_id:
            return {}
        ctx = self.context_store.get(trace_id)
        if ctx is None:
            return {}
        if isinstance(ctx, dict):
            return dict(ctx)
        # If a non-dict is stored, wrap it under a reserved key.
        return {"value": ctx}

    @staticmethod
    def _normalize(item: object) -> Envelope:
        # Allow raw payloads for input sources; they must already be targeted.
        if isinstance(item, Envelope):
            return item
        raise ValueError("WorkQueue must contain Envelope instances")
