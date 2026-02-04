from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stream_kernel.adapters.registry import AdapterRegistry, AdapterRegistryError
from stream_kernel.adapters.wiring import build_injection_registry, AdapterWiringError
from stream_kernel.application_context.injection_registry import InjectionRegistry


class _OutputPort:
    pass


@dataclass(frozen=True, slots=True)
class _FileOutput:
    path: Path


def test_build_injection_registry_binds_output_sink() -> None:
    adapter_registry = AdapterRegistry()
    adapter_registry.register(
        "output_sink",
        "file",
        lambda settings: _FileOutput(Path(settings["path"])),
    )

    adapters_cfg = {
        "output_sink": {"kind": "file", "settings": {"path": "out.txt"}},
    }
    bindings = {"output_sink": ("stream", _OutputPort)}

    registry = build_injection_registry(adapters_cfg, adapter_registry, bindings)
    assert isinstance(registry, InjectionRegistry)

    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("stream", _OutputPort)
    assert isinstance(resolved, _FileOutput)
    assert resolved.path.name == "out.txt"


def test_build_injection_registry_missing_role_fails() -> None:
    adapter_registry = AdapterRegistry()
    adapters_cfg = {}
    bindings = {"output_sink": ("stream", _OutputPort)}

    with pytest.raises(AdapterWiringError):
        build_injection_registry(adapters_cfg, adapter_registry, bindings)


def test_build_injection_registry_unknown_kind_fails() -> None:
    adapter_registry = AdapterRegistry()
    adapters_cfg = {"output_sink": {"kind": "kafka", "settings": {}}}
    bindings = {"output_sink": ("stream", _OutputPort)}

    with pytest.raises(AdapterRegistryError):
        build_injection_registry(adapters_cfg, adapter_registry, bindings)


def test_build_injection_registry_requires_adapters_mapping() -> None:
    # Adapter wiring expects a mapping root (Configuration spec §2.1).
    adapter_registry = AdapterRegistry()
    bindings = {"output_sink": ("stream", _OutputPort)}

    with pytest.raises(AdapterWiringError):
        build_injection_registry("nope", adapter_registry, bindings)  # type: ignore[arg-type]


def test_build_injection_registry_requires_role_mapping() -> None:
    # Each adapter role must point at a mapping with kind/settings (Configuration spec §2.1).
    adapter_registry = AdapterRegistry()
    bindings = {"output_sink": ("stream", _OutputPort)}

    with pytest.raises(AdapterWiringError):
        build_injection_registry({"output_sink": "nope"}, adapter_registry, bindings)


def test_build_injection_registry_supports_multiple_bindings_for_role() -> None:
    adapter_registry = AdapterRegistry()
    adapter_registry.register(
        "window_store",
        "memory",
        lambda settings: object(),
    )

    adapters_cfg = {"window_store": {"kind": "memory", "settings": {}}}
    bindings = {"window_store": [("kv", object), ("kv", str)]}

    registry = build_injection_registry(adapters_cfg, adapter_registry, bindings)
    scope = registry.instantiate_for_scenario("s1")
    assert scope.resolve("kv", object) is scope.resolve("kv", str)
