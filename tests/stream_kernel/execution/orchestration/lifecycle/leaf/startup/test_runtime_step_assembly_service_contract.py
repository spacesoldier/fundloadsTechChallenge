from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service import (
    DefaultLeafRuntimeStepAssemblyService,
    LeafRuntimeIngressEgressPlan,
)


@dataclass(frozen=True)
class _FakeMeta:
    name: str
    service: bool = False
    consumes: tuple[object, ...] = ()


@dataclass(frozen=True)
class _FakeNodeDef:
    meta: _FakeMeta


@dataclass
class _StaticIngressEgressPlanning:
    plan_result: LeafRuntimeIngressEgressPlan

    def plan(self, **_kwargs: object) -> LeafRuntimeIngressEgressPlan:
        return self.plan_result


def _capture_seed(monkeypatch, module):
    seeded: list[dict[object, list[str]]] = []

    def _seed(*, scenario_scope: object, consumers: dict[object, list[str]]) -> None:  # noqa: ARG001
        seeded.append(dict(consumers))

    monkeypatch.setattr(module, "seed_startup_consumer_bindings", _seed)
    return seeded


def _empty_ingress_egress() -> LeafRuntimeIngressEgressPlan:
    return LeafRuntimeIngressEgressPlan(
        source_steps=[],
        source_consumers={},
        source_node_names=set(),
        sink_steps=[],
        sink_consumers={},
    )


def test_leaf_runtime_step_assembly_service_merges_system_steps_and_seeds_startup_bindings(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    seeded = _capture_seed(monkeypatch, mod)

    app_context = SimpleNamespace(
        nodes=[
            _FakeNodeDef(_FakeMeta(name="business.a", service=False)),
            _FakeNodeDef(_FakeMeta(name="business.service_node", service=True)),
        ]
    )
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    bundle = SimpleNamespace(adapters={}, runtime={}, run_id="run", scenario_id="scenario")
    service = DefaultLeafRuntimeStepAssemblyService(
        ingress_egress_planning=_StaticIngressEgressPlanning(_empty_ingress_egress())
    )

    out = service.assemble_steps(
        bundle=bundle,
        app_context=app_context,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "obs.node" in out.scenario_steps
    assert "cp.node" in out.scenario_steps
    assert out.full_context_nodes == {"business.service_node", "obs.node", "cp.node"}
    assert seeded
    assert seeded[-1]["obs.token"] == ["obs.node"]
    assert seeded[-1]["cp.token"] == ["cp.node"]


def test_leaf_runtime_step_assembly_service_skips_observability_nodes_for_worker_role_but_keeps_bindings(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    seeded = _capture_seed(monkeypatch, mod)

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = DefaultLeafRuntimeStepAssemblyService(
        ingress_egress_planning=_StaticIngressEgressPlanning(_empty_ingress_egress())
    )

    out = service.assemble_steps(
        bundle=bundle,
        app_context=app_context,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "cp.node" in out.scenario_steps
    assert "obs.node" not in out.scenario_steps
    assert seeded[-1]["obs.token"] == ["obs.node"]


def test_leaf_runtime_step_assembly_service_keeps_observability_nodes_for_observability_worker(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    seeded = _capture_seed(monkeypatch, mod)

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "observability_worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = DefaultLeafRuntimeStepAssemblyService(
        ingress_egress_planning=_StaticIngressEgressPlanning(_empty_ingress_egress())
    )

    out = service.assemble_steps(
        bundle=bundle,
        app_context=app_context,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "obs.node" in out.scenario_steps
    assert seeded[-1]["obs.token"] == ["obs.node"]


def test_leaf_runtime_step_assembly_service_filters_discovered_obs_steps_for_worker_role(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    monkeypatch.setattr(
        mod,
        "build_observability_system_plan",
        lambda **_: SimpleNamespace(
            system_consumers={"obs.token": ["obs.node"]},
            system_steps=[SimpleNamespace(name="obs.node", step=lambda *_: None)],
            system_node_names=["obs.node"],
        ),
    )
    monkeypatch.setattr(
        mod,
        "build_control_plane_system_plan",
        lambda **_: SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[]),
    )
    _capture_seed(monkeypatch, mod)

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(
        steps=[
            SimpleNamespace(name="business.a", step=lambda *_: None),
            SimpleNamespace(name="system.obs.trace_dispatch", step=lambda *_: None),
            SimpleNamespace(name="system.cp.root_bootstrap", step=lambda *_: None),
        ]
    )
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = DefaultLeafRuntimeStepAssemblyService(
        ingress_egress_planning=_StaticIngressEgressPlanning(_empty_ingress_egress())
    )

    out = service.assemble_steps(
        bundle=bundle,
        app_context=app_context,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "system.obs.trace_dispatch" not in out.scenario_steps
    assert "system.cp.root_bootstrap" not in out.scenario_steps


def test_leaf_runtime_step_assembly_service_keeps_observability_and_debug_bindings_in_seeded_map(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    obs_token = object()
    debug_token = object()
    monkeypatch.setattr(
        mod,
        "build_observability_system_plan",
        lambda **_: SimpleNamespace(
            system_consumers={obs_token: ["system.obs.trace_dispatch"]},
            system_steps=[],
            system_node_names=[],
        ),
    )
    monkeypatch.setattr(
        mod,
        "build_control_plane_system_plan",
        lambda **_: SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[]),
    )
    monkeypatch.setattr(
        mod,
        "build_debug_system_plan",
        lambda **_: SimpleNamespace(
            system_consumers={debug_token: ["system.debug.message_dispatch"]},
            system_steps=[SimpleNamespace(name="system.debug.message_dispatch", step=lambda *_: [])],
            system_node_names=["system.debug.message_dispatch"],
        ),
    )
    seeded = _capture_seed(monkeypatch, mod)

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: [])])
    bundle = SimpleNamespace(adapters={}, runtime={}, run_id="run", scenario_id="scenario")
    service = DefaultLeafRuntimeStepAssemblyService(
        ingress_egress_planning=_StaticIngressEgressPlanning(_empty_ingress_egress())
    )

    out = service.assemble_steps(
        bundle=bundle,
        app_context=app_context,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "system.debug.message_dispatch" in out.scenario_steps
    assert "system.debug.message_dispatch" in out.full_context_nodes
    assert seeded[-1][obs_token] == ["system.obs.trace_dispatch"]
    assert seeded[-1][debug_token] == ["system.debug.message_dispatch"]
