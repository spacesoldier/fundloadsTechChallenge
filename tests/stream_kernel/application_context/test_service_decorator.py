from __future__ import annotations

import pytest

from types import ModuleType

from fund_load.services.prime_checker import SievePrimeChecker
from fund_load.services.window_store import InMemoryWindowStore
from stream_kernel.application_context.service import ServiceMeta, discover_services, service
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue, InMemoryTopic
from stream_kernel.platform.services.state.consumer_registry import DiscoveryConsumerRegistry
from stream_kernel.platform.services.state.context import InMemoryKvContextService


def test_service_decorator_attaches_metadata_to_class() -> None:
    # Explicit service marker should attach ServiceMeta for discovery/lint tooling.
    @service(name="sample_service")
    class _SampleService:
        pass

    meta = getattr(_SampleService, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta)
    assert meta.name == "sample_service"


def test_service_decorator_uses_class_name_by_default() -> None:
    # Service name defaults to class __name__ when not provided.
    @service()
    class _SampleService:
        pass

    meta = getattr(_SampleService, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta)
    assert meta.name == "_SampleService"


def test_service_decorator_rejects_empty_name() -> None:
    # Empty explicit names are invalid for deterministic diagnostics.
    with pytest.raises(ValueError):
        service(name="")(type("X", (), {}))


def test_inmemory_context_service_is_marked_as_service() -> None:
    # Framework context service should be explicitly marked.
    meta = getattr(InMemoryKvContextService, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta)
    assert meta.name == "context_service"


def test_project_window_store_is_marked_as_service() -> None:
    # Project state service should be discoverable by @service marker.
    meta = getattr(InMemoryWindowStore, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta)
    assert meta.name == "window_store_service"


def test_project_prime_checker_is_marked_as_service() -> None:
    # Prime checker service should be discoverable by @service marker.
    meta = getattr(SievePrimeChecker, "__service_meta__", None)
    assert isinstance(meta, ServiceMeta)
    assert meta.name == "prime_checker_service"


def test_discover_services_allows_reexport_of_same_class() -> None:
    # Re-exporting the same service class from multiple modules should not raise duplicates.
    mod_a = ModuleType("fake.services.a")
    mod_b = ModuleType("fake.services.b")
    mod_a.ctx = InMemoryKvContextService
    mod_b.ctx_alias = InMemoryKvContextService
    discovered = discover_services([mod_a, mod_b])
    assert discovered == [InMemoryKvContextService]


def test_runtime_transport_services_are_marked() -> None:
    # Runtime execution transport/routing must be discoverable as framework services.
    assert isinstance(getattr(InMemoryQueue, "__service_meta__", None), ServiceMeta)
    assert isinstance(getattr(InMemoryTopic, "__service_meta__", None), ServiceMeta)
    assert isinstance(getattr(RoutingService, "__service_meta__", None), ServiceMeta)
    assert isinstance(getattr(DiscoveryConsumerRegistry, "__service_meta__", None), ServiceMeta)
