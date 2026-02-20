from __future__ import annotations

import pytest

from stream_kernel.execution.observers.observer import ObserverFactoryContext
from stream_kernel.execution.orchestration.observability_system_nodes import TraceDispatchEvent
from stream_kernel.observability.observers.tracing import build_tracing_observer


class _Sink:
    def emit(self, _record: object) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FailingSink(_Sink):
    def emit(self, _record: object) -> None:
        raise RuntimeError("boom")


class _CollectingSink(_Sink):
    def __init__(self, collector: list[object]) -> None:
        self._collector = collector

    def emit(self, record: object) -> None:
        self._collector.append(record)


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
            adapter_instances={
                "trace_otel_otlp#0": _Sink(),
                "trace_opentracing_bridge#1": _Sink(),
            },
            run_id="r1",
            scenario_id="s1",
            node_order=["n1"],
        )
    )
    assert observer is not None


def test_build_tracing_observer_observability_exporter_failure_is_isolated() -> None:
    exported: list[object] = []

    observer = build_tracing_observer(
        ObserverFactoryContext(
                runtime={
                    "observability": {
                        "pipeline": {
                            "system_nodes": [
                                {"kind": "system.obs.trace_dispatch", "enabled": False},
                            ]
                        },
                        "tracing": {
                            "exporters": [
                                {"kind": "otel_otlp"},
                                {"kind": "opentracing_bridge"},
                            ]
                    }
                }
            },
            adapter_instances={
                "trace_otel_otlp#0": _FailingSink(),
                "trace_opentracing_bridge#1": _CollectingSink(exported),
            },
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
    assert getattr(exported[0], "trace_id", None) == "t1"


def test_build_tracing_observer_raises_when_exporter_binding_missing_in_strict_mode() -> None:
    with pytest.raises(ValueError, match="runtime.observability.tracing.exporters\\[0\\] sink binding is missing"):
        build_tracing_observer(
            ObserverFactoryContext(
                runtime={
                    "strict": True,
                    "observability": {
                        "tracing": {
                            "exporters": [
                                {"kind": "otel_otlp", "settings": {"endpoint": "http://collector:4318/v1/traces"}}
                            ]
                        }
                    },
                },
                adapter_instances={},
                run_id="r1",
                scenario_id="s1",
                node_order=["n1"],
            )
        )


def test_build_tracing_observer_skips_missing_exporter_binding_in_non_strict_mode() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "strict": False,
                "observability": {
                    "tracing": {
                        "exporters": [
                            {"kind": "otel_otlp", "settings": {"endpoint": "http://collector:4318/v1/traces"}}
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
    assert observer is None


def test_build_tracing_observer_enables_runner_dispatch_when_trace_dispatch_node_configured() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "observability": {
                    "pipeline": {
                        "system_nodes": [
                            {"kind": "system.obs.trace_dispatch", "enabled": True},
                        ]
                    },
                    "tracing": {
                        "exporters": [
                            {"kind": "otel_otlp"},
                        ]
                    },
                }
            },
            adapter_instances={"trace_otel_otlp#0": _Sink()},
            run_id="r1",
            scenario_id="s1",
            node_order=["worker"],
        )
    )
    assert observer is not None

    state = observer.before_node(node_name="worker", payload={"v": 1}, ctx={}, trace_id="t1")
    dispatched = observer.after_node(
        node_name="worker",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert isinstance(dispatched, TraceDispatchEvent)


def test_build_tracing_observer_enables_runner_dispatch_when_exporters_are_configured() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "observability": {
                    "tracing": {
                        "exporters": [
                            {"kind": "otel_otlp", "enabled": True},
                        ]
                    }
                }
            },
            adapter_instances={"trace_otel_otlp#0": _Sink()},
            run_id="r1",
            scenario_id="s1",
            node_order=["worker"],
        )
    )
    assert observer is not None

    state = observer.before_node(node_name="worker", payload={"v": 1}, ctx={}, trace_id="t1")
    dispatched = observer.after_node(
        node_name="worker",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert isinstance(dispatched, TraceDispatchEvent)


def test_build_tracing_observer_worker_role_skips_local_sinks_but_dispatches() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "__process_role": "worker",
                "tracing": {"enabled": False},
                "observability": {
                    "tracing": {
                        "exporters": [{"kind": "jsonl", "enabled": True}],
                    },
                },
            },
            adapter_instances={"trace_jsonl#0": _Sink()},
            run_id="r1",
            scenario_id="s1",
            node_order=["worker"],
        )
    )
    assert observer is not None
    state = observer.before_node(node_name="worker", payload={"v": 1}, ctx={}, trace_id="t1")
    dispatched = observer.after_node(
        node_name="worker",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert isinstance(dispatched, TraceDispatchEvent)


def test_build_tracing_observer_supervisor_transport_only_mode_skips_local_sinks_but_dispatches() -> None:
    observer = build_tracing_observer(
        ObserverFactoryContext(
            runtime={
                "strict": True,
                "platform": {"bootstrap": {"mode": "process_supervisor"}},
                "observability": {
                    "service_process": {"enabled": True, "group_name": "system.observability"},
                    "tracing": {
                        "exporters": [{"kind": "otel_otlp_logical", "enabled": True}],
                    },
                },
            },
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            node_order=["worker"],
        )
    )
    assert observer is not None
    state = observer.before_node(node_name="worker", payload={"v": 1}, ctx={}, trace_id="t1")
    dispatched = observer.after_node(
        node_name="worker",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert isinstance(dispatched, TraceDispatchEvent)
