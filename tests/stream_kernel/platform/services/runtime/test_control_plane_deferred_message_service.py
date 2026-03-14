from __future__ import annotations

from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_deferred_message import (
    InMemoryControlPlaneDeferredMessageService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDeferredMessageHoldEvent,
    ControlPlaneLeafSinkDispatchAckEvent,
)


class _TokenA:
    pass


class _TokenB:
    pass


def test_deferred_message_service_holds_and_replays_only_routable_payloads() -> None:
    registry = InMemoryConsumerRegistry({_TokenA: ["sink:a"]})
    service = InMemoryControlPlaneDeferredMessageService(
        registry=registry,
        store=InMemoryKvStore(),
    )
    service.hold(ControlPlaneDeferredMessageHoldEvent(payload=_TokenA(), source_node="source:a"))
    service.hold(ControlPlaneDeferredMessageHoldEvent(payload=_TokenB(), source_node="source:b"))

    replayed = service.collect_replayable()
    assert len(replayed) == 1
    assert isinstance(replayed[0], _TokenA)
    assert service.pending_count() == 1

    registry.register(_TokenB, ["sink:b"])
    replayed_after_binding = service.collect_replayable()
    assert len(replayed_after_binding) == 1
    assert isinstance(replayed_after_binding[0], _TokenB)
    assert service.pending_count() == 0


def test_deferred_message_service_ignores_non_event_inputs() -> None:
    service = InMemoryControlPlaneDeferredMessageService(
        registry=InMemoryConsumerRegistry(),
        store=InMemoryKvStore(),
    )
    service.hold(object())
    assert service.pending_count() == 0


def test_deferred_message_service_drops_transient_sink_ack_tails() -> None:
    service = InMemoryControlPlaneDeferredMessageService(
        registry=InMemoryConsumerRegistry(),
        store=InMemoryKvStore(),
    )
    service.hold(
        ControlPlaneDeferredMessageHoldEvent(
            payload=ControlPlaneLeafSinkDispatchAckEvent(
                target_group="execution.ingress",
                worker_id="execution.ingress#1",
                request_id="req-1",
                source_target="source:source",
                payload_class="dict",
                tombstone_output=False,
            ),
            source_node="system.cp.leaf_reply_dispatch",
        )
    )

    assert service.pending_count() == 0
