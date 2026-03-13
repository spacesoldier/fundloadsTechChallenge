from __future__ import annotations

from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_consumer_registry import (
    InMemoryControlPlaneDynamicConsumerRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
    ControlPlaneDiscoveryEntityRecord,
)


class _TokenA:
    pass


class _TokenB:
    pass


@node(name="system.test.dynamic_consumer", consumes=[_TokenA], emits=[])
class _DynamicConsumerNodeA:
    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        _ = (msg, ctx)
        return []


@node(name="system.test.dynamic_consumer", consumes=[_TokenB], emits=[])
class _DynamicConsumerNodeB:
    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        _ = (msg, ctx)
        return []


def _record(*, qualname: str) -> ControlPlaneDiscoveryEntityRecord:
    return ControlPlaneDiscoveryEntityRecord(
        entity_kind="node",
        entity_id=f"{__name__}:{qualname}",
        source_scope="platform",
        module=__name__,
        qualname=qualname,
        meta={"name": "system.test.dynamic_consumer"},
    )


def test_dynamic_consumer_routing_replaces_previous_bindings_for_same_node() -> None:
    registry = InMemoryConsumerRegistry()
    registry.register(_TokenA, ["system.test.dynamic_consumer"])
    service = InMemoryControlPlaneDynamicConsumerRoutingService(
        registry=registry,
        store=InMemoryKvStore(),
    )

    service.apply_discovery_records((_record(qualname="_DynamicConsumerNodeA"),))
    assert registry.get_consumers(_TokenA) == ["system.test.dynamic_consumer"]

    service.apply_discovery_records((_record(qualname="_DynamicConsumerNodeB"),))
    assert registry.get_consumers(_TokenB) == ["system.test.dynamic_consumer"]
    assert registry.get_consumers(_TokenA) == []


def test_dynamic_consumer_routing_remove_node_bindings_clears_registry_entries() -> None:
    registry = InMemoryConsumerRegistry()
    registry.register(_TokenB, ["system.test.dynamic_consumer"])
    service = InMemoryControlPlaneDynamicConsumerRoutingService(
        registry=registry,
        store=InMemoryKvStore(),
    )
    service.apply_discovery_records((_record(qualname="_DynamicConsumerNodeB"),))

    service.remove_node_bindings(("system.test.dynamic_consumer",))

    assert registry.get_consumers(_TokenB) == []
    assert registry.has_node("system.test.dynamic_consumer") is False


def test_dynamic_consumer_routing_apply_bindings_registers_sink_and_source_nodes() -> None:
    registry = InMemoryConsumerRegistry()
    service = InMemoryControlPlaneDynamicConsumerRoutingService(
        registry=registry,
        store=InMemoryKvStore(),
    )

    service.apply_bindings(
        (
            ControlPlaneConsumerBindingRecord(
                token=_TokenA,
                node_names=("source:events", "sink:writer"),
            ),
        )
    )

    assert registry.get_consumers(_TokenA) == ["source:events", "sink:writer"]
