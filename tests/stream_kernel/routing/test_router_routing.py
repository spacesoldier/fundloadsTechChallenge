from __future__ import annotations

import pytest

# Routing rules are defined in docs/framework/initial_stage/Routing semantics.md.
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import Router


class X:
    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, X) and other.value == self.value


def _router() -> Router:
    # Consumer map is discovery-ordered for deterministic routing (Routing semantics §5.1).
    return Router(consumers={X: ["B", "C"]}, strict=True)


def test_router_default_fanout_by_type() -> None:
    # Default fan-out: no target means deliver to all consumers of the type (§5.1).
    router = _router()
    payload = X("x")
    deliveries = router.route([payload])
    assert deliveries == [("B", payload), ("C", payload)]


def test_router_target_overrides_type_fanout() -> None:
    # Targeted envelope should bypass type fan-out (§5.2).
    router = _router()
    payload = X("x")
    deliveries = router.route([Envelope(payload=payload, target="C")])
    assert deliveries == [("C", payload)]


def test_router_multiple_targets_preserve_order() -> None:
    # Explicit target list should be delivered in the given order (§5.3).
    router = _router()
    payload = X("x")
    deliveries = router.route([Envelope(payload=payload, target=["B", "C"])])
    assert deliveries == [("B", payload), ("C", payload)]


def test_router_mixed_outputs_are_routed_independently() -> None:
    # Mixed outputs: one targeted, one default fan-out (§5.4).
    router = _router()
    payload_target = X("targeted")
    payload_fanout = X("fanout")
    deliveries = router.route(
        [
            Envelope(payload=payload_target, target="B"),
            payload_fanout,
        ]
    )
    assert deliveries == [
        ("B", payload_target),
        ("B", payload_fanout),
        ("C", payload_fanout),
    ]


def test_router_raises_on_unknown_target_in_strict_mode() -> None:
    # Unknown target should fail fast in strict mode (§5.8).
    router = _router()
    payload = X("x")
    with pytest.raises(ValueError):
        router.route([Envelope(payload=payload, target="Missing")])


def test_router_excludes_source_from_default_fanout() -> None:
    # To avoid accidental self-loops, source node is excluded from default fan-out.
    router = _router()
    payload = X("x")
    deliveries = router.route([payload], source="B")
    assert deliveries == [("C", payload)]


def test_router_allows_explicit_self_target_even_with_source_filter() -> None:
    # Explicit target must still allow self-delivery; source filter is default-fanout only.
    router = _router()
    payload = X("x")
    deliveries = router.route([Envelope(payload=payload, target="B")], source="B")
    assert deliveries == [("B", payload)]


def test_router_requires_explicit_target_for_self_loop_in_strict_mode() -> None:
    # If source-filter removes the only consumer, strict mode should fail and require explicit target.
    router = Router(consumers={X: ["B"]}, strict=True)
    with pytest.raises(ValueError):
        router.route([X("x")], source="B")


def test_router_drops_self_loop_without_target_in_non_strict_mode() -> None:
    # Non-strict mode may drop ambiguous self-loop candidates when no other consumers remain.
    router = Router(consumers={X: ["B"]}, strict=False)
    deliveries = router.route([X("x")], source="B")
    assert deliveries == []
