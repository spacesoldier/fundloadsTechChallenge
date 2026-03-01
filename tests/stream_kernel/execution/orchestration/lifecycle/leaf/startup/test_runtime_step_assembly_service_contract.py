from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class _FakeMeta:
    name: str
    service: bool = False
    consumes: tuple[object, ...] = ()


@dataclass(frozen=True)
class _FakeNodeDef:
    meta: _FakeMeta


@dataclass
class _ConsumerRegistry:
    values: dict[object, list[str]]

    def get_consumers(self, token: object) -> list[str]:
        return list(self.values.get(token, []))

    def register(self, token: object, names: list[str]) -> None:
        self.values[token] = list(names)


def test_leaf_runtime_step_assembly_service_merges_system_steps_and_consumers(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    monkeypatch.setattr(mod, "build_lifecycle_system_plan", lambda **_: _system_plan("lc"))

    app_context = SimpleNamespace(
        nodes=[
            _FakeNodeDef(_FakeMeta(name="business.a", service=False)),
            _FakeNodeDef(_FakeMeta(name="business.service_node", service=True)),
        ]
    )
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="business.a", step=lambda *_: None)]
    )
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "obs.node" in out.scenario_steps
    assert "cp.node" in out.scenario_steps
    assert "lc.node" in out.scenario_steps
    assert out.full_context_nodes == {"business.service_node", "obs.node", "cp.node", "lc.node"}
    assert registry.values["obs.token"] == ["obs.node"]
    assert registry.values["cp.token"] == ["cp.node"]
    assert registry.values["lc.token"] == ["lc.node"]


def test_leaf_runtime_step_assembly_service_skips_observability_nodes_for_worker_role(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    monkeypatch.setattr(mod, "build_lifecycle_system_plan", lambda **_: _system_plan("lc"))

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "cp.node" in out.scenario_steps
    assert "lc.node" in out.scenario_steps
    assert "obs.node" not in out.scenario_steps
    # Worker runtime is transport-only for observability: keep consumer routes,
    # but do not mount local observability dispatch nodes.
    assert registry.values["obs.token"] == ["obs.node"]


def test_leaf_runtime_step_assembly_service_keeps_observability_nodes_for_observability_worker(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    monkeypatch.setattr(mod, "build_lifecycle_system_plan", lambda **_: _system_plan("lc"))

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "observability_worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "obs.node" in out.scenario_steps
    assert registry.values["obs.token"] == ["obs.node"]


def test_leaf_runtime_step_assembly_service_filters_discovered_obs_steps_for_worker_role(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    def _system_plan(prefix: str):
        return SimpleNamespace(
            system_consumers={f"{prefix}.token": [f"{prefix}.node"]},
            system_steps=[SimpleNamespace(name=f"{prefix}.node", step=lambda *_: None)],
            system_node_names=[f"{prefix}.node"],
        )

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: _system_plan("obs"))
    monkeypatch.setattr(mod, "build_control_plane_system_plan", lambda **_: _system_plan("cp"))
    monkeypatch.setattr(mod, "build_lifecycle_system_plan", lambda **_: _system_plan("lc"))

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(
        steps=[
            SimpleNamespace(name="business.a", step=lambda *_: None),
            SimpleNamespace(name="system.obs.trace_dispatch", step=lambda *_: None),
            SimpleNamespace(name="system.cp.root_bootstrap", step=lambda *_: None),
        ]
    )
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert "business.a" in out.scenario_steps
    assert "system.obs.trace_dispatch" not in out.scenario_steps
    assert "system.cp.root_bootstrap" not in out.scenario_steps


def test_leaf_runtime_step_assembly_service_mounts_transport_handoff_node_for_root_transport_only_observability(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    obs_token = object()
    obs_plan = SimpleNamespace(
        system_consumers={obs_token: ["system.obs.trace_dispatch"]},
        system_steps=[],
        system_node_names=[],
    )

    def _cp_plan(**_kwargs):
        return SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[])

    def _lc_plan(**_kwargs):
        return SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[])

    handoff_node_name = "system.transport.handoff.observability_dispatch"
    handoff_step = SimpleNamespace(name=handoff_node_name, step=lambda *_: None)
    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: obs_plan)
    monkeypatch.setattr(mod, "build_control_plane_system_plan", _cp_plan)
    monkeypatch.setattr(mod, "build_lifecycle_system_plan", _lc_plan)
    monkeypatch.setattr(
        mod,
        "_build_transport_only_observability_handoff_plan",
        lambda **_kwargs: ([handoff_step], {obs_token: [handoff_node_name]}, {handoff_node_name}),
    )

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert handoff_node_name in out.scenario_steps
    assert registry.values[obs_token] == [handoff_node_name]


def test_leaf_runtime_step_assembly_service_mounts_transport_handoff_node_for_worker_transport_only_observability(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service as mod

    obs_token = object()
    obs_plan = SimpleNamespace(
        system_consumers={obs_token: ["system.obs.trace_dispatch"]},
        system_steps=[],
        system_node_names=[],
    )

    handoff_node_name = "system.transport.handoff.observability_dispatch"
    handoff_step = SimpleNamespace(name=handoff_node_name, step=lambda *_: None)

    monkeypatch.setattr(mod, "build_observability_system_plan", lambda **_: obs_plan)
    monkeypatch.setattr(
        mod,
        "build_control_plane_system_plan",
        lambda **_kwargs: SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[]),
    )
    monkeypatch.setattr(
        mod,
        "build_lifecycle_system_plan",
        lambda **_kwargs: SimpleNamespace(system_consumers={}, system_steps=[], system_node_names=[]),
    )
    monkeypatch.setattr(
        mod,
        "_build_transport_only_observability_handoff_plan",
        lambda **_kwargs: ([handoff_step], {obs_token: [handoff_node_name]}, {handoff_node_name}),
    )

    app_context = SimpleNamespace(nodes=[_FakeNodeDef(_FakeMeta(name="business.a", service=False))])
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="business.a", step=lambda *_: None)])
    registry = _ConsumerRegistry(values={})
    bundle = SimpleNamespace(
        adapters={},
        runtime={"__process_role": "worker"},
        run_id="run",
        scenario_id="scenario",
    )
    service = mod.DefaultLeafRuntimeStepAssemblyService()

    out = service.assemble_steps(
        execution_builder=SimpleNamespace(),
        bundle=bundle,
        app_context=app_context,
        consumer_registry=registry,
        scenario_scope=SimpleNamespace(),
        scenario=scenario,
        step_names=["business.a"],
        adapter_instances={},
        adapter_registry=None,
    )

    assert handoff_node_name in out.scenario_steps
    assert registry.values[obs_token] == [handoff_node_name]
