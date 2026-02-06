from __future__ import annotations

import pytest

# Envelope invariants are part of routing semantics (docs/framework/initial_stage/Routing semantics.md).
from stream_kernel.routing.envelope import Envelope


def test_envelope_requires_payload() -> None:
    # Payload is mandatory to avoid "empty" routed messages (Routing semantics §1).
    with pytest.raises(ValueError):
        Envelope(payload=None)  # type: ignore[arg-type]


def test_envelope_allows_string_target() -> None:
    # Single-node target is allowed and preserved.
    env = Envelope(payload=object(), target="NodeA")
    assert env.target == "NodeA"


def test_envelope_rejects_empty_string_target() -> None:
    # Empty target is invalid: it cannot be routed deterministically.
    with pytest.raises(ValueError):
        Envelope(payload=object(), target="")


def test_envelope_allows_list_target() -> None:
    # Multi-target routing uses a list of node names (Routing semantics §5.3).
    env = Envelope(payload=object(), target=["NodeA", "NodeB"])
    assert env.target == ["NodeA", "NodeB"]


def test_envelope_rejects_empty_target_list() -> None:
    # An empty list would mean "no route" but looks like an explicit target.
    with pytest.raises(ValueError):
        Envelope(payload=object(), target=[])


def test_envelope_rejects_non_string_target_list_items() -> None:
    # Targets must be node names (strings), not arbitrary objects.
    with pytest.raises(ValueError):
        Envelope(payload=object(), target=["NodeA", 123])  # type: ignore[list-item]


def test_envelope_rejects_empty_target_list_items() -> None:
    # Empty node names are invalid.
    with pytest.raises(ValueError):
        Envelope(payload=object(), target=["NodeA", ""])


def test_envelope_rejects_empty_topic() -> None:
    # Topic/channels are optional, but must be non-empty if provided.
    with pytest.raises(ValueError):
        Envelope(payload=object(), topic="")


def test_envelope_rejects_non_string_topic() -> None:
    # Topics are strings; other types are not supported.
    with pytest.raises(ValueError):
        Envelope(payload=object(), topic=123)  # type: ignore[arg-type]


def test_envelope_allows_trace_id() -> None:
    # trace_id is optional but must be a non-empty string if provided.
    env = Envelope(payload=object(), trace_id="t-1")
    assert env.trace_id == "t-1"


def test_envelope_rejects_empty_trace_id() -> None:
    with pytest.raises(ValueError):
        Envelope(payload=object(), trace_id="")
