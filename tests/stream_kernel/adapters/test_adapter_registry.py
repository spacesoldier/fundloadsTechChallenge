from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stream_kernel.adapters.registry import AdapterRegistry, AdapterRegistryError


@dataclass(frozen=True, slots=True)
class _FileInput:
    path: Path


@dataclass(frozen=True, slots=True)
class _FileOutput:
    path: Path


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


def test_adapter_registry_requires_settings_mapping() -> None:
    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: _FileInput(Path(settings["path"])))

    with pytest.raises(AdapterRegistryError):
        registry.build("input_source", {"kind": "file", "settings": "nope"})
