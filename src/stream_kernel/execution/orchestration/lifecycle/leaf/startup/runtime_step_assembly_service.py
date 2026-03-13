from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.control_plane import build_control_plane_system_plan
from stream_kernel.execution.orchestration.startup_bindings import (
    merge_consumer_maps,
    seed_startup_consumer_bindings,
)
from stream_kernel.platform.services.runtime.control_plane_consumer_registry import (
    ControlPlaneDynamicConsumerRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
)
from stream_kernel.execution.orchestration.observability_system_nodes import (
    build_observability_system_plan,
)
from stream_kernel.execution.orchestration.debug_system_nodes import (
    build_debug_system_plan,
)
from stream_kernel.kernel.scenario import StepSpec


@dataclass(frozen=True, slots=True)
class LeafRuntimeStepAssemblyResult:
    scenario_steps: dict[str, object]
    full_context_nodes: set[str]


@dataclass(frozen=True, slots=True)
class LeafRuntimeIngressEgressPlan:
    source_steps: list[StepSpec]
    source_consumers: dict[type[Any], list[str]]
    source_node_names: set[str]
    sink_steps: list[StepSpec]
    sink_consumers: dict[type[Any], list[str]]


@runtime_checkable
class LeafRuntimeIngressEgressPlanningService(Protocol):
    def plan(
        self,
        *,
        bundle_adapters: dict[str, object],
        adapter_instances: dict[str, object],
        adapter_registry: object | None,
        scenario_scope: object,
        app_context: object,
        step_names: list[str],
        runtime: dict[str, object] | None,
        run_id: str,
        scenario_id: str,
    ) -> LeafRuntimeIngressEgressPlan:
        raise NotImplementedError


@service(name="leaf_runtime_ingress_egress_planning_service")
@dataclass(slots=True)
class DefaultLeafRuntimeIngressEgressPlanningService(LeafRuntimeIngressEgressPlanningService):
    def plan(
        self,
        *,
        bundle_adapters: dict[str, object],
        adapter_instances: dict[str, object],
        adapter_registry: object | None,
        scenario_scope: object,
        app_context: object,
        step_names: list[str],
        runtime: dict[str, object] | None,
        run_id: str,
        scenario_id: str,
    ) -> LeafRuntimeIngressEgressPlan:
        if not bundle_adapters or adapter_registry is None:
            return LeafRuntimeIngressEgressPlan(
                source_steps=[],
                source_consumers={},
                source_node_names=set(),
                sink_steps=[],
                sink_consumers={},
            )

        from stream_kernel.execution.orchestration.builder import build_sink_runtime_nodes
        from stream_kernel.execution.orchestration.source_ingress import build_source_ingress_plan

        source_ingress = build_source_ingress_plan(
            adapters=bundle_adapters,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
            scenario_scope=scenario_scope,
            run_id=run_id,
            scenario_id=scenario_id,
            runtime=runtime,
        )
        in_graph_consumes = {
            token
            for node_def in getattr(app_context, "nodes", [])
            if getattr(getattr(node_def, "meta", None), "name", None) in set(step_names)
            for token in getattr(getattr(node_def, "meta", None), "consumes", [])
        }
        sink_nodes, sink_consumers = build_sink_runtime_nodes(
            adapters=bundle_adapters,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
            in_graph_consumes=in_graph_consumes,
        )
        sink_steps = [StepSpec(name=name, step=step) for name, step in sink_nodes.items()]
        return LeafRuntimeIngressEgressPlan(
            source_steps=list(source_ingress.source_steps),
            source_consumers=dict(source_ingress.source_consumers),
            source_node_names=set(source_ingress.source_node_names),
            sink_steps=sink_steps,
            sink_consumers=dict(sink_consumers),
        )


@runtime_checkable
class LeafRuntimeStepAssemblyService(Protocol):
    def assemble_steps(
        self,
        *,
        bundle: object,
        app_context: object,
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
    ingress_egress_planning: object | None = inject.service(LeafRuntimeIngressEgressPlanningService)

    def assemble_steps(
        self,
        *,
        bundle: object,
        app_context: object,
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
        include_local_observability_nodes = not (
            isinstance(process_role, str) and process_role == "worker"
        )

        source_step_names: set[str] = set()
        combined_steps = [
            spec
            for spec in list(getattr(scenario, "steps", []))
            if not (
                isinstance(getattr(spec, "name", None), str)
                and str(getattr(spec, "name")).startswith(
                    ("system.cp.", "system.lifecycle.", "system.obs.", "system.debug.")
                )
            )
        ]
        ingress_egress = self._resolve_ingress_egress_planning().plan(
            bundle_adapters=bundle_adapters,
            adapter_instances=adapter_instances,
            adapter_registry=adapter_registry,
            scenario_scope=scenario_scope,
            app_context=app_context,
            step_names=step_names,
            runtime=runtime if isinstance(runtime, dict) else None,
            run_id=run_id,
            scenario_id=scenario_id,
        )
        if ingress_egress.source_steps:
            combined_steps = [*ingress_egress.source_steps, *combined_steps]
        if ingress_egress.sink_steps:
            combined_steps.extend(ingress_egress.sink_steps)
        source_step_names = set(ingress_egress.source_node_names)

        observability_system = build_observability_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        observability_steps_to_mount = list(observability_system.system_steps)
        if not include_local_observability_nodes:
            observability_steps_to_mount = [
                spec
                for spec in observability_steps_to_mount
                if _is_observability_transport_step(getattr(spec, "name", ""))
            ]
        combined_steps.extend(observability_steps_to_mount)

        debug_system = build_debug_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        combined_steps.extend(debug_system.system_steps)

        control_plane_system = build_control_plane_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
        )
        combined_steps.extend(control_plane_system.system_steps)
        startup_bindings = merge_consumer_maps(
            control_plane_system.system_consumers,
            observability_system.system_consumers,
            debug_system.system_consumers,
            ingress_egress.source_consumers,
            ingress_egress.sink_consumers,
        )
        seed_startup_consumer_bindings(
            scenario_scope=scenario_scope,
            consumers=startup_bindings,
        )
        self._preload_runtime_consumer_registry(
            scenario_scope=scenario_scope,
            consumers=startup_bindings,
        )

        scenario_steps = {spec.name: spec.step for spec in combined_steps}
        full_context_nodes = (
            {
                getattr(node_def.meta, "name")
                for node_def in getattr(app_context, "nodes", [])
                if bool(getattr(node_def.meta, "service", False))
            }
            | source_step_names
            | set(control_plane_system.system_node_names)
            | {spec.name for spec in observability_steps_to_mount if isinstance(spec.name, str)}
            | set(debug_system.system_node_names)
        )
        return LeafRuntimeStepAssemblyResult(
            scenario_steps=scenario_steps,
            full_context_nodes=full_context_nodes,
        )

    def _resolve_ingress_egress_planning(self) -> LeafRuntimeIngressEgressPlanningService:
        candidate = self.ingress_egress_planning
        if isinstance(candidate, LeafRuntimeIngressEgressPlanningService):
            return candidate
        return DefaultLeafRuntimeIngressEgressPlanningService()

    @staticmethod
    def _preload_runtime_consumer_registry(
        *,
        scenario_scope: object,
        consumers: dict[object, list[str]],
    ) -> None:
        # Compatibility preload for boundary-only execution helpers that do not
        # run full control-plane bootstrap before first routed payload.
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return
        try:
            routing = resolve("service", ControlPlaneDynamicConsumerRoutingService)
        except Exception:
            return
        apply_bindings = getattr(routing, "apply_bindings", None)
        if not callable(apply_bindings):
            return
        records: list[ControlPlaneConsumerBindingRecord] = []
        for token, node_names in consumers.items():
            if not isinstance(token, type):
                continue
            normalized = tuple(name for name in node_names if isinstance(name, str) and name)
            if not normalized:
                continue
            records.append(ControlPlaneConsumerBindingRecord(token=token, node_names=normalized))
        if not records:
            return
        try:
            apply_bindings(tuple(records))
        except Exception:
            return


def _is_observability_transport_step(node_name: object) -> bool:
    return isinstance(node_name, str) and node_name.startswith("system.transport.handoff.")

__all__ = [
    "LeafRuntimeIngressEgressPlan",
    "LeafRuntimeIngressEgressPlanningService",
    "DefaultLeafRuntimeIngressEgressPlanningService",
    "LeafRuntimeStepAssemblyResult",
    "LeafRuntimeStepAssemblyService",
    "DefaultLeafRuntimeStepAssemblyService",
]
