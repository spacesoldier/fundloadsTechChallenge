from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry, AdapterRegistryError


@dataclass(frozen=True, slots=True)
class _FileInput:
    path: Path


@dataclass(frozen=True, slots=True)
class _FileOutput:
    path: Path


class _InToken:
    pass


class _OutToken:
    pass


def test_adapter_registry_resolves_known_kind() -> None:
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))
    registry.register("output_sink", "file", lambda settings: _FileOutput(Path(settings["path"])))

    input_adapter = registry.build("input_source", {"kind": "file", "settings": {"path": "in.ndjson"}})
    output_adapter = registry.build("output_sink", {"kind": "file", "settings": {"path": "out.txt"}})

    assert isinstance(input_adapter, _FileInput)
    assert input_adapter.path.name == "in.ndjson"
    assert isinstance(output_adapter, _FileOutput)
    assert output_adapter.path.name == "out.txt"


def test_adapter_registry_unknown_kind_fails() -> None:
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.build("input_source", {"kind": "kafka", "settings": {}})


def test_adapter_registry_rejects_duplicate_registration() -> None:
    # Registry must reject duplicate (role, kind) registrations (Adapter registry spec).
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))


def test_adapter_registry_requires_mapping_config() -> None:
    # Adapter config is validated as a mapping (Configuration spec §2.1).
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.build("input_source", "nope")  # type: ignore[arg-type]


def test_adapter_registry_requires_kind_string() -> None:
    # Adapter kind must be explicit and string-typed (Configuration spec §2.1).
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.build("input_source", {"kind": 1, "settings": {}})


def test_adapter_registry_requires_settings_mapping() -> None:
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.build("input_source", {"kind": "file", "settings": "nope"})


def test_adapter_registry_exposes_registered_factory_metadata() -> None:
    # Contracts are sourced from @adapter metadata on factory functions.
    registry = AdapterRegistry()

    @adapter(consumes=[_InToken], emits=[_OutToken])
    def _factory(settings: dict[str, object]) -> object:
        return object()

    registry.register("input_source", "file", _factory)
    meta = registry.get_meta("input_source", "file")
    assert meta is not None
    assert list(meta.consumes) == [_InToken]
    assert list(meta.emits) == [_OutToken]


def test_adapter_registry_returns_none_meta_for_plain_factory() -> None:
    # Plain factories remain supported; they just have no typed DAG contract metadata.
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))
    assert registry.get_meta("input_source", "file") is None
