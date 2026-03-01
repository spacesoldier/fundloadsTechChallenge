from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane import build_control_plane_system_plan
from stream_kernel.execution.orchestration.lifecycle.root.startup.planning import build_lifecycle_system_plan
from stream_kernel.execution.orchestration.observability_system_nodes import (
    build_observability_system_plan,
)
from stream_kernel.execution.transport.handoff.system_nodes import (
    build_transport_observability_handoff_plan,
)
from stream_kernel.kernel.scenario import StepSpec


@dataclass(frozen=True, slots=True)
class LeafRuntimeStepAssemblyResult:
    scenario_steps: dict[str, object]
    full_context_nodes: set[str]


@runtime_checkable
class LeafRuntimeStepAssemblyService(Protocol):
    def assemble_steps(
        self,
        *,
        execution_builder: object,
        bundle: object,
        app_context: object,
        consumer_registry: object,
        scenario_scope: object,
        scenario: object,
        step_names: list[str],
        adapter_instances: dict[str, object],
        adapter_registry: object | None,
    ) -> LeafRuntimeStepAssemblyResult:
        raise NotImplementedError


@service(name="leaf_runtime_step_assembly_service")
@dataclass(slots=True)
class DefaultLeafRuntimeStepAssemblyService(LeafRuntimeStepAssemblyService):
    def assemble_steps(
        self,
        *,
        execution_builder: object,
        bundle: object,
        app_context: object,
        consumer_registry: object,
        scenario_scope: object,
        scenario: object,
        step_names: list[str],
        adapter_instances: dict[str, object],
        adapter_registry: object | None,
    ) -> LeafRuntimeStepAssemblyResult:
        bundle_adapters = dict(getattr(bundle, "adapters", {}) or {})
        runtime = getattr(bundle, "runtime")
        run_id = getattr(bundle, "run_id")
        scenario_id = getattr(bundle, "scenario_id")
        process_role = runtime.get("__process_role") if isinstance(runtime, dict) else None
        include_observability_nodes = not (isinstance(process_role, str) and process_role == "worker")

        source_step_names: set[str] = set()
        combined_steps = [
            spec
            for spec in list(getattr(scenario, "steps", []))
            if not (
                isinstance(getattr(spec, "name", None), str)
                and str(getattr(spec, "name")).startswith(("system.cp.", "system.lifecycle.", "system.obs."))
            )
        ]
        if bundle_adapters and adapter_registry is not None:
            source_ingress = execution_builder.build_source_ingress_plan(
                adapters=bundle_adapters,
                adapter_instances=adapter_instances,
                adapter_registry=adapter_registry,
                scenario_scope=scenario_scope,
                run_id=run_id,
                scenario_id=scenario_id,
                runtime=runtime,
            )
            for token, node_names in source_ingress.source_consumers.items():
                _append_consumers(consumer_registry, token, node_names)

            in_graph_consumes = {
                token
                for node_def in getattr(app_context, "nodes", [])
                if getattr(getattr(node_def, "meta", None), "name", None) in set(step_names)
                for token in getattr(getattr(node_def, "meta", None), "consumes", [])
            }
            sink_nodes, sink_consumers = execution_builder.build_sink_runtime_nodes(
                adapters=bundle_adapters,
                adapter_instances=adapter_instances,
                adapter_registry=adapter_registry,
                in_graph_consumes=in_graph_consumes,
            )
            for token, node_names in sink_consumers.items():
                _append_consumers(consumer_registry, token, node_names)

            combined_steps = [*source_ingress.source_steps, *combined_steps]
            combined_steps.extend([StepSpec(name=name, step=step) for name, step in sink_nodes.items()])
            source_step_names = set(source_ingress.source_node_names)

        observability_system = build_observability_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        # Transport-only observability mode (no local system.obs.* steps) mounts
        # an explicit transport handoff node so dispatch follows
        # node->service->adapter rails in both root and worker processes.
        use_transport_handoff_for_observability = (
            not observability_system.system_steps
            and bool(observability_system.system_consumers)
        )
        if use_transport_handoff_for_observability:
            handoff_steps, handoff_consumers, _handoff_nodes = _build_transport_only_observability_handoff_plan(
                scenario_scope=scenario_scope
            )
            for token, node_names in handoff_consumers.items():
                _append_consumers(consumer_registry, token, node_names)
            combined_steps.extend(handoff_steps)
        else:
            for token, node_names in observability_system.system_consumers.items():
                _append_consumers(consumer_registry, token, node_names)
            if include_observability_nodes:
                combined_steps.extend(observability_system.system_steps)

        control_plane_system = build_control_plane_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        for token, node_names in control_plane_system.system_consumers.items():
            _append_consumers(consumer_registry, token, node_names)
        combined_steps.extend(control_plane_system.system_steps)

        lifecycle_system = build_lifecycle_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        for token, node_names in lifecycle_system.system_consumers.items():
            _append_consumers(consumer_registry, token, node_names)
        combined_steps.extend(lifecycle_system.system_steps)

        scenario_steps = {spec.name: spec.step for spec in combined_steps}
        full_context_nodes = (
            {
                getattr(node_def.meta, "name")
                for node_def in getattr(app_context, "nodes", [])
                if bool(getattr(node_def.meta, "service", False))
            }
            | source_step_names
            | set(control_plane_system.system_node_names)
            | set(lifecycle_system.system_node_names)
            | (
                _build_transport_only_observability_handoff_plan(scenario_scope=scenario_scope)[2]
                if use_transport_handoff_for_observability
                else (
                    set(observability_system.system_node_names)
                    if include_observability_nodes
                    else set()
                )
            )
            | (
                set(observability_system.system_node_names)
                if include_observability_nodes and not use_transport_handoff_for_observability
                else set()
            )
        )
        return LeafRuntimeStepAssemblyResult(
            scenario_steps=scenario_steps,
            full_context_nodes=full_context_nodes,
        )


def _append_consumers(consumer_registry: object, token: object, node_names: list[str]) -> None:
    get_consumers = getattr(consumer_registry, "get_consumers", None)
    register = getattr(consumer_registry, "register", None)
    if not callable(get_consumers) or not callable(register):
        return
    existing = list(get_consumers(token))
    register(token, [*existing, *node_names])


def _build_transport_only_observability_handoff_plan(
    *,
    scenario_scope: object | None = None,
) -> tuple[list[StepSpec], dict[object, list[str]], set[str]]:
    steps, consumers, nodes = build_transport_observability_handoff_plan(
        scenario_scope=scenario_scope
    )
    return (list(steps), dict(consumers), set(nodes))


__all__ = [
    "LeafRuntimeStepAssemblyResult",
    "LeafRuntimeStepAssemblyService",
    "DefaultLeafRuntimeStepAssemblyService",
]
