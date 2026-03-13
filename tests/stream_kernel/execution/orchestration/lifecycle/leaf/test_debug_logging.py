from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.lifecycle.leaf import debug_logging as mod
from stream_kernel.observability.domain.debug import DebugMessage


@dataclass(slots=True)
class _Sink:
    messages: list[DebugMessage]

    def publish(self, message: DebugMessage) -> None:
        self.messages.append(message)


def _bind_test_service() -> mod.DefaultLeafLifecycleDebugLoggingService:
    service = mod.DefaultLeafLifecycleDebugLoggingService(
        store=mod.InMemoryLeafLifecycleDebugStore(),
        runtime_debug_buffer=None,
    )
    return service


def _disable_logger(*, group_name: str = "execution.alpha", worker_id: str = "execution.alpha#1") -> None:
    service = _bind_test_service()
    mod.configure_leaf_debug_logging(
        runtime={"platform": {"debug": {"leaf_debug_enabled": False}}},
        group_name=group_name,
        worker_id=worker_id,
        service=service,
    )
    mod.bind_leaf_debug_sink(None, service=service)


def test_leaf_debug_logging_queues_and_flushes_into_bound_sink() -> None:
    service = _bind_test_service()
    sink = _Sink(messages=[])
    try:
        mod.configure_leaf_debug_logging(
            runtime={
                "platform": {
                    "debug": {
                        "leaf_debug_enabled": True,
                        "leaf_debug_write_to_file": False,
                    }
                }
            },
            group_name="execution.alpha",
            worker_id="execution.alpha#1",
            service=service,
        )
        mod.leaf_debug_log(event="leaf.custom.event", answer=42, service=service)
        mod.bind_leaf_debug_sink(sink, service=service)
        events = [item.event for item in sink.messages]
        assert "leaf.debug_logger.configured" in events
        assert "leaf.custom.event" in events
    finally:
        _disable_logger()


def test_leaf_debug_logging_disable_clears_sink_binding() -> None:
    service = _bind_test_service()
    sink = _Sink(messages=[])
    try:
        mod.configure_leaf_debug_logging(
            runtime={
                "platform": {
                    "debug": {
                        "leaf_debug_enabled": True,
                        "leaf_debug_write_to_file": False,
                    }
                }
            },
            group_name="execution.alpha",
            worker_id="execution.alpha#1",
            service=service,
        )
        mod.bind_leaf_debug_sink(sink, service=service)
        mod.leaf_debug_log(event="leaf.enabled.event", service=service)
        before = len(sink.messages)
        mod.configure_leaf_debug_logging(
            runtime={"platform": {"debug": {"leaf_debug_enabled": False}}},
            group_name="execution.alpha",
            worker_id="execution.alpha#1",
            service=service,
        )
        mod.leaf_debug_log(event="leaf.disabled.event", service=service)
        assert len(sink.messages) == before
    finally:
        _disable_logger()


def test_leaf_debug_logging_without_service_is_noop() -> None:
    service = _bind_test_service()
    sink = _Sink(messages=[])
    try:
        mod.configure_leaf_debug_logging(
            runtime={
                "platform": {
                    "debug": {
                        "leaf_debug_enabled": True,
                        "leaf_debug_write_to_file": False,
                    }
                }
            },
            group_name="execution.alpha",
            worker_id="execution.alpha#1",
            service=service,
        )
        mod.bind_leaf_debug_sink(sink, service=service)
        mod.leaf_debug_log(event="leaf.noop.without.service")
        events = [item.event for item in sink.messages]
        assert "leaf.noop.without.service" not in events
    finally:
        _disable_logger()
