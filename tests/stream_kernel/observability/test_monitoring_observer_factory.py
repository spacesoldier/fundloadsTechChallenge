from __future__ import annotations

import asyncio

from stream_kernel.execution.observers.observer import ObserverFactoryContext
from stream_kernel.observability.domain.monitoring import MonitoringMessage
from stream_kernel.observability.observers.monitoring import build_monitoring_dispatch_observer


def test_build_monitoring_dispatch_observer_returns_none_without_monitoring_adapters() -> None:
    observer = build_monitoring_dispatch_observer(
        ObserverFactoryContext(
            runtime={},
            adapter_instances={},
            run_id="run",
            scenario_id="scenario",
            node_order=[],
        )
    )
    assert observer is None


def test_build_monitoring_dispatch_observer_emits_monitoring_message_to_adapter_sink() -> None:
    class _Sink:
        def __init__(self) -> None:
            self.messages: list[MonitoringMessage] = []

        def emit(self, message: MonitoringMessage) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return None

    sink = _Sink()
    observer = build_monitoring_dispatch_observer(
        ObserverFactoryContext(
            runtime={},
            adapter_instances={"monitoring_prometheus": sink},
            run_id="run",
            scenario_id="scenario",
            node_order=[],
        )
    )
    assert observer is not None

    callback = getattr(observer, "on_monitoring_event", None)
    assert callable(callback)
    callback(
        event=MonitoringMessage(
            name="worker_queue_depth",
            status="sample",
            details={"group_name": "execution.features", "queue_depth": 3},
        ),
        trace_id=None,
        attributes={},
    )
    assert sink.messages
    assert sink.messages[-1].name == "worker_queue_depth"


def test_build_monitoring_dispatch_observer_async_fallback_does_not_require_to_thread() -> None:
    class _Sink:
        def __init__(self) -> None:
            self.messages: list[MonitoringMessage] = []

        def emit(self, message: MonitoringMessage) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return None

    import stream_kernel.observability.observers.monitoring as monitoring_module
    assert not hasattr(monitoring_module, "asyncio")

    sink = _Sink()
    observer = build_monitoring_dispatch_observer(
        ObserverFactoryContext(
            runtime={},
            adapter_instances={"monitoring_prometheus": sink},
            run_id="run",
            scenario_id="scenario",
            node_order=[],
        )
    )
    assert observer is not None

    callback = getattr(observer, "on_monitoring_event_async", None)
    assert callable(callback)
    asyncio.run(
        callback(
            event=MonitoringMessage(
                name="worker_queue_depth",
                status="sample",
                details={"group_name": "execution.features", "queue_depth": 3},
            ),
            trace_id=None,
            attributes={},
        )
    )
    assert sink.messages
    assert sink.messages[-1].name == "worker_queue_depth"
