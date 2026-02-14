from __future__ import annotations

from stream_kernel.execution.observers.observer import ObserverFactoryContext
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
            node_order=[],
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
            node_order=[],
        )
    )
    assert observer is not None


def test_build_tracing_observer_builds_from_observability_exporters() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "observability": {
                    "tracing": {
                        "exporters": [
                            {"kind": "otel_otlp", "settings": {"endpoint": "http://collector:4318/v1/traces"}},
                            {"kind": "opentracing_bridge", "settings": {"bridge_name": "legacy"}},
                        ]
                    }
                }
            },
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            node_order=["n1"],
        )
    )
    assert observer is not None


def test_build_tracing_observer_observability_exporter_failure_is_isolated() -> None:
    exported: list[dict[str, object]] = []

    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "observability": {
                    "tracing": {
                        "exporters": [
                            {
                                "kind": "otel_otlp",
                                "settings": {
                                    "endpoint": "http://collector:4318/v1/traces",
                                    "_export_fn": lambda _span: (_ for _ in ()).throw(RuntimeError("boom")),
                                },
                            },
                            {
                                "kind": "opentracing_bridge",
                                "settings": {"bridge_name": "legacy", "_emit_fn": lambda span: exported.append(span)},
                            },
                        ]
                    }
                }
            },
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            node_order=["n1"],
        )
    )
    assert observer is not None
    state = observer.before_node(node_name="n1", payload={"v": 1}, ctx={}, trace_id="t1")
    observer.after_node(
        node_name="n1",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state,
    )
    observer.on_run_end()
    assert len(exported) == 1
    assert exported[0]["trace_id"] == "t1"
