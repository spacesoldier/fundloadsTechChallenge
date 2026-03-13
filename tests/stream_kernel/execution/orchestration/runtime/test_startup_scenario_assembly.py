from __future__ import annotations

from stream_kernel.execution.orchestration.runtime.startup_scenario_assembly import (
    assemble_runtime_startup_scenario,
)
from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.platform.services.runtime.control_plane_events import ControlPlaneInitEvent
from stream_kernel.routing.envelope import Envelope


def _step(name: str) -> StepSpec:
    return StepSpec(name=name, step=lambda _payload, _ctx: [])


def test_assemble_runtime_startup_scenario_root_process_supervisor_excludes_business_steps() -> None:
    runtime = {"platform": {"bootstrap": {"mode": "process_supervisor"}}}
    scenario = Scenario(
        scenario_id="s",
        steps=(
            _step("biz.a"),
            _step("biz.b"),
        ),
    )

    result = assemble_runtime_startup_scenario(
        runtime=runtime,
        scenario=scenario,
        source_steps=[_step("source.one")],
        control_plane_steps=[_step("system.cp.one")],
        lifecycle_steps=[_step("system.lifecycle.one")],
        observability_steps=[_step("system.obs.one")],
        sink_steps=[_step("sink.one")],
        source_inputs=[Envelope(payload={"kind": "seed"}, target="source.one")],
    )

    step_names = [spec.name for spec in result.scenario.steps]
    assert step_names == [
        "source.one",
        "system.cp.one",
        "system.lifecycle.one",
        "system.obs.one",
        "sink.one",
    ]
    assert len(result.inputs) == 2
    assert isinstance(result.inputs[0], Envelope)
    assert isinstance(result.inputs[1], Envelope)
    assert isinstance(result.inputs[0].payload, ControlPlaneInitEvent)
    assert result.inputs[0].target == "system.cp.consumer_registry_bindings_bootstrap"


def test_assemble_runtime_startup_scenario_local_mode_keeps_business_steps() -> None:
    runtime = {"platform": {"bootstrap": {"mode": "local"}}}
    scenario = Scenario(
        scenario_id="s",
        steps=(
            _step("biz.a"),
            _step("biz.b"),
        ),
    )

    result = assemble_runtime_startup_scenario(
        runtime=runtime,
        scenario=scenario,
        source_steps=[_step("source.one")],
        control_plane_steps=[_step("system.cp.one")],
        lifecycle_steps=[],
        observability_steps=[],
        sink_steps=[],
        source_inputs=[],
    )

    step_names = [spec.name for spec in result.scenario.steps]
    assert step_names == [
        "source.one",
        "system.cp.one",
        "biz.a",
        "biz.b",
    ]
    assert result.inputs == []


def test_assemble_runtime_startup_scenario_passes_init_discovery_payload() -> None:
    runtime = {"platform": {"bootstrap": {"mode": "process_supervisor"}}}
    scenario = Scenario(scenario_id="s", steps=())

    result = assemble_runtime_startup_scenario(
        runtime=runtime,
        scenario=scenario,
        source_steps=[],
        control_plane_steps=[],
        lifecycle_steps=[],
        observability_steps=[],
        sink_steps=[],
        source_inputs=[],
        init_discovery={
            "root_runtime_prepare": {
                "run_id": "run-1",
                "scenario_id": "s-1",
                "config": {"runtime": {}},
                "adapters": {"a": {"kind": "x"}},
                "discovery_modules": ("m1", "m2"),
            }
        },
    )

    assert len(result.inputs) == 1
    assert isinstance(result.inputs[0], Envelope)
    assert isinstance(result.inputs[0].payload, ControlPlaneInitEvent)
    assert isinstance(result.inputs[0].payload.discovery, dict)
    assert "root_runtime_prepare" in result.inputs[0].payload.discovery
