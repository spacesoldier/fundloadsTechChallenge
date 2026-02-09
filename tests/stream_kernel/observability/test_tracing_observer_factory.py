from __future__ import annotations

from stream_kernel.execution.observer import ObserverFactoryContext
from stream_kernel.observability.observers.tracing import build_tracing_observer


class _Sink:
    def emit(self, _record: object) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_build_tracing_observer_returns_none_when_disabled() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={"tracing": {"enabled": False}},
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            step_specs=[],
        )
    )
    assert observer is None


def test_build_tracing_observer_builds_when_enabled_and_sink_present() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={"tracing": {"enabled": True, "sink": {"name": "trace_jsonl"}}},
            adapter_instances={"trace_jsonl": _Sink()},
            run_id="r1",
            scenario_id="s1",
            step_specs=[],
        )
    )
    assert observer is not None
