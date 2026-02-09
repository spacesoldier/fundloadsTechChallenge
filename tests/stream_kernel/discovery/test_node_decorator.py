from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from stream_kernel.kernel.node import NodeMeta, node


class LoadAttempt:
    pass


class AttemptWithKeys:
    pass


def test_node_decorator_attaches_metadata_to_class() -> None:
    # @node on classes should attach NodeMeta with provided fields.
    @node(
        name="compute_time_keys",
        stage="time",
        consumes=[LoadAttempt],
        emits=[AttemptWithKeys],
    )
    @dataclass(frozen=True, slots=True)
    class ComputeTimeKeys:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    meta = getattr(ComputeTimeKeys, "__node_meta__", None)
    assert isinstance(meta, NodeMeta)
    assert meta.name == "compute_time_keys"
    assert meta.stage == "time"
    assert meta.consumes == [LoadAttempt]
    assert meta.emits == [AttemptWithKeys]


def test_node_decorator_attaches_metadata_to_function() -> None:
    # @node on functions should attach NodeMeta with defaults.
    @node(name="parse", stage="parse")
    def parse(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    meta = getattr(parse, "__node_meta__", None)
    assert isinstance(meta, NodeMeta)
    assert meta.name == "parse"
    assert meta.stage == "parse"
    assert meta.consumes == []
    assert meta.emits == []


def test_node_decorator_uses_defaults_when_optional_fields_missing() -> None:
    # Missing optional metadata should fall back to safe defaults.
    @node(name="noop")
    def noop(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    meta = getattr(noop, "__node_meta__", None)
    assert isinstance(meta, NodeMeta)
    assert meta.name == "noop"
    assert meta.stage == ""
    assert meta.consumes == []
    assert meta.emits == []
    assert meta.service is False


def test_node_decorator_supports_service_nodes() -> None:
    # Service nodes are marked explicitly to receive full runtime context.
    @node(name="trace_observer", service=True)
    def trace_observer(msg: object, ctx: object | None) -> list[object]:
        return [msg]

    meta = getattr(trace_observer, "__node_meta__", None)
    assert isinstance(meta, NodeMeta)
    assert meta.service is True


def test_node_decorator_rejects_empty_name() -> None:
    # Empty names are invalid and must fail fast.
    with pytest.raises(ValueError):
        node(name="")(lambda msg, ctx: [msg])


def test_node_decorator_rejects_duplicate_emits_entries() -> None:
    # Duplicate emits should be rejected for determinism (Node and stage specs).
    with pytest.raises(ValueError):
        node(name="dup", emits=[LoadAttempt, LoadAttempt])(lambda msg, ctx: [msg])


def test_node_decorator_rejects_duplicate_consumes_entries() -> None:
    # Duplicate consumes should be rejected for determinism (Node and stage specs).
    with pytest.raises(ValueError):
        node(name="dup", consumes=[LoadAttempt, LoadAttempt])(lambda msg, ctx: [msg])
