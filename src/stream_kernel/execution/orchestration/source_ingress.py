from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.platform.services.context import ContextService
from stream_kernel.routing.envelope import Envelope

_NO_PAYLOAD = object()


@dataclass(frozen=True, slots=True)
class BootstrapControl:
    # Framework control payload used to trigger source nodes on routing rails.
    target: str


@dataclass(slots=True)
class SourceBootstrapNode:
    # Source adapter wrapper executed inside runner graph.
    role: str
    node_name: str
    adapter: object
    context_service: ContextService
    run_id: str
    scenario_id: str
    _sequence: int = 0
    _iterator: object | None = None
    _next_payload: object = _NO_PAYLOAD
    _primed: bool = False
    exhausted: bool = False

    def __call__(self, _msg: object, _ctx: object | None) -> list[Envelope]:
        if not self._primed:
            self._prime_next()
            self._primed = True

        if self.exhausted or self._next_payload is _NO_PAYLOAD:
            return []

        payload = self._next_payload
        self._prime_next()
        self._sequence += 1
        trace_id = f"{self.run_id}:{self.role}:{self._sequence}"
        self.context_service.seed(
            trace_id=trace_id,
            payload=payload,
            run_id=self.run_id,
            scenario_id=self.scenario_id,
        )
        outputs = [Envelope(payload=payload, trace_id=trace_id)]
        if not self.exhausted:
            # Re-schedule source polling on the same rails as regular node routing.
            outputs.append(
                Envelope(
                    payload=BootstrapControl(target=self.node_name),
                    target=self.node_name,
                )
            )
        return outputs

    def _prime_next(self) -> None:
        if self.exhausted:
            return
        if self._iterator is None:
            read = getattr(self.adapter, "read", None)
            if not callable(read):
                self.exhausted = True
                self._next_payload = _NO_PAYLOAD
                return
            self._iterator = iter(read())
        try:
            self._next_payload = next(self._iterator)
        except StopIteration:
            self.exhausted = True
            self._next_payload = _NO_PAYLOAD


@dataclass(frozen=True, slots=True)
class SourceIngressPlan:
    # Source ingress runtime artifacts attached to scenario execution.
    source_steps: list[StepSpec] = field(default_factory=list)
    source_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    bootstrap_inputs: list[Envelope] = field(default_factory=list)
    source_node_names: set[str] = field(default_factory=set)


def build_source_ingress_plan(
    *,
    adapters: dict[str, object],
    adapter_instances: dict[str, object],
    adapter_registry: AdapterRegistry | None,
    scenario_scope: ScenarioScope,
    run_id: str,
    scenario_id: str,
) -> SourceIngressPlan:
    # Build executable source ingress nodes from adapter contracts (consumes=[] and emits!=[]).
    context_service = scenario_scope.resolve("service", ContextService)
    source_nodes: dict[str, object] = {}
    source_consumers: dict[type[Any], list[str]] = {}
    for role in sorted(adapter_instances.keys()):
        cfg = adapters.get(role)
        if not isinstance(cfg, dict):
            continue
        meta = _resolve_adapter_meta(role, adapter_registry=adapter_registry)
        adapter = adapter_instances[role]
        has_reader = callable(getattr(adapter, "read", None))
        if not has_reader:
            continue
        if meta is not None and (meta.consumes or not meta.emits):
            continue
        node_name = f"source:{role}"
        source_nodes[node_name] = SourceBootstrapNode(
            role=role,
            node_name=node_name,
            adapter=adapter,
            context_service=context_service,
            run_id=run_id,
            scenario_id=scenario_id,
        )
        source_consumers.setdefault(BootstrapControl, []).append(node_name)
    if adapters and not source_nodes:
        raise ValueError("at least one source adapter (consumes=[], emits!=[]) with read() must be configured")

    source_steps = [StepSpec(name=name, step=step) for name, step in source_nodes.items()]
    bootstrap_inputs = [
        Envelope(payload=BootstrapControl(target=target), target=target)
        for target in source_nodes.keys()
    ]
    return SourceIngressPlan(
        source_steps=source_steps,
        source_consumers=source_consumers,
        bootstrap_inputs=bootstrap_inputs,
        source_node_names=set(source_nodes.keys()),
    )


def _resolve_adapter_meta(role: str, *, adapter_registry: AdapterRegistry | None):
    if adapter_registry is not None:
        meta = adapter_registry.get_meta(role, role)
        if meta is not None:
            return meta
    return None
