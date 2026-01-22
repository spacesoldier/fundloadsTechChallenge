from __future__ import annotations

from datetime import UTC, datetime

import pytest

# Context contract is documented in docs/implementation/kernel/Context Spec.md.
from fund_load.kernel.context import Context, ContextFactory


def test_context_factory_sets_required_fields() -> None:
    # Factory must populate trace_id/run_id/scenario_id and initialize containers.
    factory = ContextFactory(run_id="run1", scenario_id="baseline")
    ctx = factory.new(line_no=1)
    assert ctx.trace_id
    assert ctx.run_id == "run1"
    assert ctx.scenario_id == "baseline"
    assert ctx.line_no == 1
    assert isinstance(ctx.received_at, datetime)
    assert ctx.received_at.tzinfo is UTC
    assert ctx.tags == {}
    assert ctx.metrics == {}
    assert ctx.notes == []
    assert ctx.errors == []
    assert ctx.trace == []
    assert ctx.flags == {}


def test_context_is_unique_per_event() -> None:
    # Each event gets a fresh Context; trace_id must be distinct.
    factory = ContextFactory(run_id="run1", scenario_id="baseline")
    ctx1 = factory.new(line_no=1)
    ctx2 = factory.new(line_no=2)
    assert ctx1 is not ctx2
    assert ctx1.trace_id != ctx2.trace_id


def test_tag_helper_enforces_string_values() -> None:
    # Context.tag should enforce string values (Context Spec).
    ctx = ContextFactory(run_id="run1", scenario_id="baseline").new(line_no=1)
    ctx.tag("k", "v")
    assert ctx.tags["k"] == "v"
    with pytest.raises(TypeError):
        ctx.tag("k2", 1)  # type: ignore[arg-type]


def test_metrics_helper_enforces_numeric_values() -> None:
    # Context.metric_set should enforce numeric types.
    ctx = ContextFactory(run_id="run1", scenario_id="baseline").new(line_no=1)
    ctx.metric_set("m", 1.5)
    assert ctx.metrics["m"] == 1.5
    with pytest.raises(TypeError):
        ctx.metric_set("m2", "1")  # type: ignore[arg-type]


def test_notes_are_append_only() -> None:
    # Context.note appends in order.
    ctx = ContextFactory(run_id="run1", scenario_id="baseline").new(line_no=1)
    ctx.note("a")
    ctx.note("b")
    assert ctx.notes == ["a", "b"]


def test_error_adds_structured_record() -> None:
    # Context.error should add a structured record with defaults.
    ctx = ContextFactory(run_id="run1", scenario_id="baseline").new(line_no=1)
    ctx.error("PARSE_ERROR", "bad json", step="ParseLoadAttempt")
    assert len(ctx.errors) == 1
    err = ctx.errors[0]
    assert err.code == "PARSE_ERROR"
    assert err.message == "bad json"
    assert err.step == "ParseLoadAttempt"
    assert err.details == {}


def test_flags_set_and_read() -> None:
    # Context flags are boolean toggles.
    ctx = ContextFactory(run_id="run1", scenario_id="baseline").new(line_no=1)
    ctx.set_flag("dropped")
    assert ctx.is_flag("dropped") is True
