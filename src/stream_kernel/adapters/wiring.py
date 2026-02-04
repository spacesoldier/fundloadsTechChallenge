from __future__ import annotations

from typing import Any

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.application_context.injection_registry import InjectionRegistry


class AdapterWiringError(ValueError):
    # Raised when adapter wiring cannot be built from config.
    pass


def build_injection_registry(
    adapters: dict[str, object],
    registry: AdapterRegistry,
    bindings: dict[str, tuple[str, type[Any]]],
) -> InjectionRegistry:
    # Build an InjectionRegistry from adapter config using the AdapterRegistry.
    if not isinstance(adapters, dict):
        raise AdapterWiringError("adapters must be a mapping")

    injection = InjectionRegistry()

    for role, binding in bindings.items():
        if role not in adapters:
            raise AdapterWiringError(f"Missing adapter config for role: {role}")
        role_cfg = adapters[role]
        if not isinstance(role_cfg, dict):
            raise AdapterWiringError(f"adapters.{role} must be a mapping")
        adapter = registry.build(role, role_cfg)
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection.register_factory(port_type, data_type, lambda _a=adapter: _a)
        else:
            port_type, data_type = binding
            injection.register_factory(port_type, data_type, lambda _a=adapter: _a)

    return injection
