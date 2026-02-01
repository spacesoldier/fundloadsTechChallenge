from __future__ import annotations

# Step contract is documented in docs/implementation/kernel/Step Contract Spec.md.
import pytest

from stream_kernel.kernel.step import Filter, Map, Step, Tap


def test_map_outputs_one_value() -> None:
    # Map must output exactly one message per input.
    step = Map(lambda msg, ctx: msg + 1)
    out = list(step(1, ctx=None))
    assert out == [2]


def test_filter_passes_or_drops() -> None:
    # Filter drops when predicate is false and passes when true.
    step = Filter(lambda msg, ctx: msg % 2 == 0)
    assert list(step(2, ctx=None)) == [2]
    assert list(step(3, ctx=None)) == []


def test_tap_preserves_message() -> None:
    # Tap applies side effect and preserves the message.
    seen: list[int] = []

    def _tap(msg, ctx):
        seen.append(msg)

    step = Tap(_tap)
    out = list(step(5, ctx=None))
    assert out == [5]
    assert seen == [5]


def test_step_protocol_default_raises() -> None:
    # Direct Step protocol calls should raise to catch wiring errors early.
    class _StepOnly(Step[int, int]):
        pass

    with pytest.raises(NotImplementedError):
        _StepOnly()(1, ctx=None)
