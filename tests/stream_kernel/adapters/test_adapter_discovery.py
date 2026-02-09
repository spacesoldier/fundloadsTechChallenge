from __future__ import annotations

from types import ModuleType

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.discovery import AdapterDiscoveryError, discover_adapters


class _PortType:
    pass


@adapter(name="reader", kind="file.line_reader", binds=[("stream", _PortType)])
def _reader(settings: dict[str, object]) -> object:
    return object()


@adapter(name="writer", kind="file.line_writer", binds=[("stream", _PortType)])
def _writer(settings: dict[str, object]) -> object:
    return object()


def test_discover_adapters_collects_by_name() -> None:
    # Adapter discovery should register decorated factories by adapter name.
    module = ModuleType("fake.adapters")
    module.reader = _reader
    module.writer = _writer
    discovered = discover_adapters([module])
    assert set(discovered.keys()) == {"reader", "writer"}
    assert callable(discovered["reader"])
    assert callable(discovered["writer"])


def test_discover_adapters_rejects_duplicate_name() -> None:
    # Duplicate adapter names must fail fast to keep runtime deterministic.
    module_a = ModuleType("fake.adapters.a")
    module_b = ModuleType("fake.adapters.b")
    module_a.reader = _reader
    @adapter(name="reader", kind="file.reader2", binds=[("stream", _PortType)])
    def _reader2(settings: dict[str, object]) -> object:
        return object()
    module_b.reader = _reader2
    with pytest.raises(AdapterDiscoveryError):
        discover_adapters([module_a, module_b])


def test_discover_adapters_allows_reexport_of_same_factory() -> None:
    # Re-exporting the same factory from multiple modules should not be treated as a conflict.
    module_a = ModuleType("fake.adapters.a")
    module_b = ModuleType("fake.adapters.b")
    module_a.reader = _reader
    module_b.reader_alias = _reader
    discovered = discover_adapters([module_a, module_b])
    assert set(discovered.keys()) == {"reader"}
    assert discovered["reader"] is _reader
