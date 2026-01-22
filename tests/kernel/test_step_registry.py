from __future__ import annotations

import pytest

# StepRegistry contract is documented in docs/implementation/kernel/Step Registry Spec.md.
from fund_load.kernel.step_registry import StepRegistry, UnknownStepError


def test_step_registry_resolves_known_key() -> None:
    # Registry should resolve a known step name to its factory.
    registry = StepRegistry()
    registry.register("alpha", lambda cfg, wiring: lambda msg, ctx: [msg])
    factory = registry.get("alpha")
    assert callable(factory)


def test_step_registry_unknown_key_raises() -> None:
    # Unknown step should fail fast with explicit error.
    registry = StepRegistry()
    with pytest.raises(UnknownStepError):
        registry.get("missing")
