from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.application_context.inject import inject, Injected
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


class EventA:
    pass


@dataclass
class _StreamPort:
    name: str


@dataclass
class _DomainService:
    name: str


@dataclass
class _KvStreamPort:
    name: str


@dataclass
class _RequestPort:
    name: str


@dataclass
class _ResponsePort:
    name: str


@dataclass
class _QueuePort:
    name: str


@dataclass
class _TopicPort:
    name: str


class _ContextKVStore(KVStore):
    # Marker KV contract for DI isolation by role.
    pass


class _BadContextKVStore(KVStore):
    # Invalid marker contract: adds unsupported public API.
    def list_keys(self) -> list[str]:
        return []


def test_inject_descriptor_carries_port_and_type() -> None:
    # Injected descriptor should store port and data type metadata.
    dep = inject.stream(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "stream"
    assert dep.data_type is EventA


def test_inject_descriptor_carries_optional_qualifier() -> None:
    # Qualifier should be preserved for scoped binding resolution.
    dep = inject.stream(EventA, qualifier="primary")
    assert isinstance(dep, Injected)
    assert dep.qualifier == "primary"


def test_inject_service_descriptor_carries_port_and_type() -> None:
    # Service injection should carry "service" port metadata with service class.
    dep = inject.service(_DomainService)
    assert isinstance(dep, Injected)
    assert dep.port_type == "service"
    assert dep.data_type is _DomainService


def test_inject_kv_stream_descriptor_carries_port_and_type() -> None:
    # KV-stream injection should carry "kv_stream" port metadata.
    dep = inject.kv_stream(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "kv_stream"
    assert dep.data_type is EventA


def test_inject_request_descriptor_carries_port_and_type() -> None:
    # Request injection should carry "request" port metadata.
    dep = inject.request(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "request"
    assert dep.data_type is EventA


def test_inject_response_descriptor_carries_port_and_type() -> None:
    # Response injection should carry "response" port metadata.
    dep = inject.response(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "response"
    assert dep.data_type is EventA


def test_inject_queue_descriptor_carries_port_and_type() -> None:
    # Queue injection should carry "queue" port metadata.
    dep = inject.queue(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "queue"
    assert dep.data_type is EventA


def test_inject_topic_descriptor_carries_port_and_type() -> None:
    # Topic injection should carry "topic" port metadata.
    dep = inject.topic(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "topic"
    assert dep.data_type is EventA


def test_inject_kv_descriptor_accepts_marker_kv_contract() -> None:
    # KV injection accepts marker subclasses that do not extend the base API.
    dep = inject.kv(_ContextKVStore)
    assert isinstance(dep, Injected)
    assert dep.port_type == "kv"
    assert dep.data_type is _ContextKVStore


def test_inject_kv_descriptor_rejects_extended_kv_contract() -> None:
    # Adding extra public methods to KV contracts is forbidden.
    with pytest.raises(ValueError):
        inject.kv(_BadContextKVStore)


def test_inject_rejects_empty_qualifier() -> None:
    # Empty qualifiers would create ambiguous registrations and must be rejected.
    with pytest.raises(ValueError):
        inject.kv(_ContextKVStore, qualifier="")


def test_inject_resolves_from_scope() -> None:
    # Injected descriptor should resolve from scenario scope.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.stream(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _StreamPort)
    assert resolved.name == "A"


def test_inject_resolves_qualified_binding_from_scope() -> None:
    # Qualifier selects the matching binding when multiple bindings share contract and port.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("primary"), qualifier="primary")
    reg.register_factory("stream", EventA, lambda: _StreamPort("fallback"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.stream(EventA, qualifier="primary")
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _StreamPort)
    assert resolved.name == "primary"


def test_inject_service_resolves_from_scope() -> None:
    # Service injection should resolve using the same scope lookup mechanism.
    reg = InjectionRegistry()
    reg.register_factory("service", _DomainService, lambda: _DomainService("svc"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.service(_DomainService)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _DomainService)
    assert resolved.name == "svc"


def test_inject_kv_stream_resolves_from_scope() -> None:
    # KV-stream injection should resolve using scenario scope lookup.
    reg = InjectionRegistry()
    reg.register_factory("kv_stream", EventA, lambda: _KvStreamPort("kvs"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.kv_stream(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _KvStreamPort)
    assert resolved.name == "kvs"


def test_inject_request_resolves_from_scope() -> None:
    # Request injection should resolve using scenario scope lookup.
    reg = InjectionRegistry()
    reg.register_factory("request", EventA, lambda: _RequestPort("req"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.request(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _RequestPort)
    assert resolved.name == "req"


def test_inject_response_resolves_from_scope() -> None:
    # Response injection should resolve using scenario scope lookup.
    reg = InjectionRegistry()
    reg.register_factory("response", EventA, lambda: _ResponsePort("resp"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.response(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _ResponsePort)
    assert resolved.name == "resp"


def test_inject_queue_resolves_from_scope() -> None:
    # Queue injection should resolve using scenario scope lookup.
    reg = InjectionRegistry()
    reg.register_factory("queue", EventA, lambda: _QueuePort("queue"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.queue(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _QueuePort)
    assert resolved.name == "queue"


def test_inject_topic_resolves_from_scope() -> None:
    # Topic injection should resolve using scenario scope lookup.
    reg = InjectionRegistry()
    reg.register_factory("topic", EventA, lambda: _TopicPort("topic"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.topic(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _TopicPort)
    assert resolved.name == "topic"


def test_inject_missing_binding_raises() -> None:
    # Missing binding should raise from resolve().
    reg = InjectionRegistry()
    scope = reg.instantiate_for_scenario("s1")
    dep = inject.stream(EventA)

    with pytest.raises(Exception):
        dep.resolve(scope)


def test_inject_kv_resolves_marker_contract_from_scope() -> None:
    # KV marker contracts should resolve through regular DI scope lookup.
    reg = InjectionRegistry()
    port = InMemoryKvStore()
    reg.register_factory("kv", _ContextKVStore, lambda _p=port: _p)
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.kv(_ContextKVStore)
    resolved = dep.resolve(scope)
    assert resolved is port
