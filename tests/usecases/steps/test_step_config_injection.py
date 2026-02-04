from __future__ import annotations

from decimal import Decimal

# Config injection is described in docs/framework/initial_stage/Injection and strict mode.md.
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.ports.prime_checker import PrimeChecker
from fund_load.ports.window_store import WindowReadPort, WindowWritePort
from fund_load.usecases.steps.compute_features import ComputeFeatures
from fund_load.usecases.steps.compute_time_keys import ComputeTimeKeys
from fund_load.usecases.steps.evaluate_policies import EvaluatePolicies
from fund_load.usecases.steps.update_windows import UpdateWindows
from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry


class _FakePrimeChecker:
    # Minimal fake for PrimeChecker port (docs/implementation/ports/PrimeChecker.md).
    def is_prime(self, id_num: int) -> bool:
        return id_num in {2, 3, 5, 7, 11}


def _registry() -> InjectionRegistry:
    # Provide ports needed by steps so ApplicationContext can inject dependencies.
    registry = InjectionRegistry()
    registry.register_factory("kv", PrimeChecker, lambda: _FakePrimeChecker())
    store = InMemoryWindowStore()
    registry.register_factory("kv", WindowReadPort, lambda _s=store: _s)
    registry.register_factory("kv", WindowWritePort, lambda _s=store: _s)
    return registry


def test_compute_features_reads_node_slice_config() -> None:
    # Step 04 config must come from nodes.compute_features.* (newgen config policy).
    ctx = ApplicationContext()
    ctx.discover([__import__("fund_load.usecases.steps", fromlist=["*"])])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["compute_features"],
        wiring={
            "config": {
                "nodes": {
                    "compute_features": {
                        "monday_multiplier": {
                            "enabled": True,
                            "multiplier": Decimal("3.0"),
                            "apply_to": "amount",
                        },
                        "prime_gate": {"enabled": True},
                    }
                }
            },
            "injection_registry": _registry(),
        },
    )

    step = scenario.steps[0].step
    assert isinstance(step, ComputeFeatures)
    assert step.monday_multiplier_enabled is True
    assert step.monday_multiplier == Decimal("3.0")
    assert step.apply_to == "amount"
    assert step.prime_enabled is True


def test_evaluate_policies_reads_node_slice_config() -> None:
    # Step 05 limits/prime_gate must come from nodes.evaluate_policies.* (newgen config policy).
    ctx = ApplicationContext()
    ctx.discover([__import__("fund_load.usecases.steps", fromlist=["*"])])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["evaluate_policies"],
        wiring={
            "config": {
                "nodes": {
                    "evaluate_policies": {
                        "limits": {
                            "daily_attempts": 3,
                            "daily_amount": Decimal("5000.00"),
                            "weekly_amount": Decimal("20000.00"),
                        },
                        "prime_gate": {
                            "enabled": True,
                            "amount_cap": Decimal("9999.00"),
                            "global_per_day": 1,
                        },
                    }
                }
            },
            "injection_registry": _registry(),
        },
    )

    step = scenario.steps[0].step
    assert isinstance(step, EvaluatePolicies)
    assert step.daily_attempt_limit == 3
    assert step.daily_amount_limit == Decimal("5000.00")
    assert step.weekly_amount_limit == Decimal("20000.00")
    assert step.prime_enabled is True
    assert step.prime_amount_cap == Decimal("9999.00")
    assert step.prime_global_per_day == 1


def test_update_windows_reads_node_slice_config() -> None:
    # Step 06 prime gate toggle must come from nodes.update_windows.daily_prime_gate.enabled.
    ctx = ApplicationContext()
    ctx.discover([__import__("fund_load.usecases.steps", fromlist=["*"])])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["update_windows"],
        wiring={
            "config": {
                "nodes": {"update_windows": {"daily_prime_gate": {"enabled": True}}}
            },
            "injection_registry": _registry(),
        },
    )

    step = scenario.steps[0].step
    assert isinstance(step, UpdateWindows)
    assert step.prime_gate_enabled is True


def test_compute_time_keys_reads_node_slice_config() -> None:
    # Step 02 week_start must come from nodes.compute_time_keys.week_start.
    ctx = ApplicationContext()
    ctx.discover([__import__("fund_load.usecases.steps", fromlist=["*"])])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["compute_time_keys"],
        wiring={"config": {"nodes": {"compute_time_keys": {"week_start": "SUN"}}}},
    )

    step = scenario.steps[0].step
    assert isinstance(step, ComputeTimeKeys)
    assert step.week_start == "SUN"
