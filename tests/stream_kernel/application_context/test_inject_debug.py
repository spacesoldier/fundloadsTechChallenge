from __future__ import annotations

from stream_kernel.application_context.application_context import _apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.observability import ObservabilityPipelineService


class _Token:
    pass


class _Port:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def send(self, payload: object) -> str:
        self.calls.append(payload)
        return "ok"


class _LogPort:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def write(self, payload: object) -> str:
        self.calls.append(payload)
        return "written"


class _Pipeline:
    def __init__(self) -> None:
        self.records: list[tuple[LogMessage, str | None, dict[str, object] | None]] = []

    def emit_log_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        if isinstance(event, LogMessage):
            self.records.append((event, trace_id, attributes))
        return []


class _Service:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def ping(self, payload: object) -> str:
        self.calls.append(payload)
        return "pong"


class _PollingPort:
    def recv(self, timeout: float | None = None) -> object:
        _ = timeout
        return None


def test_inject_debug_wraps_stream_ports_and_emits_runtime_debug_logs(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    monkeypatch.setenv("STREAM_KERNEL_LOGICAL_RUN_ID", "run")
    monkeypatch.setenv("STREAM_KERNEL_RUN_INSTANCE_ID", "launch-xyz")
    monkeypatch.setenv("STREAM_KERNEL_PROCESS_GROUP", "execution.ingress")
    monkeypatch.setenv("STREAM_KERNEL_WORKER_ID", "execution.ingress#1")

    registry = InjectionRegistry()
    pipeline = _Pipeline()
    port = _Port()
    registry.register_factory("stream", _Token, lambda: port)
    registry.register_factory("service", ObservabilityPipelineService, lambda: pipeline)
    scope = registry.instantiate_for_scenario("s1")

    class _Holder:
        dep = inject.stream(_Token)

    holder = _Holder()
    _apply_injection(holder, scope, strict=True)

    result = holder.dep.send({"x": 1})
    assert result == "ok"
    assert port.calls == [{"x": 1}]
    assert len(pipeline.records) == 1
    message, trace_id, attrs = pipeline.records[0]
    assert trace_id is None
    assert attrs == {"channel": "runtime_debug"}
    assert message.level == "debug"
    assert message.fields.get("event") == "runtime.inject.port_call"
    assert message.fields.get("debug_channel") == "runtime_debug"
    assert message.fields.get("owner_field") == "dep"
    assert message.fields.get("port_type") == "stream"
    assert message.fields.get("method") == "send"
    assert message.fields.get("process_group") == "execution.ingress"
    assert message.fields.get("worker_id") == "execution.ingress#1"
    assert message.fields.get("__run_instance_id") == "launch-xyz"
    assert isinstance(message.fields.get("caller_function"), str)


def test_inject_debug_wraps_service_ports_and_preserves_isinstance(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    monkeypatch.setenv("STREAM_KERNEL_LOGICAL_RUN_ID", "run")
    monkeypatch.setenv("STREAM_KERNEL_RUN_INSTANCE_ID", "launch-xyz")
    monkeypatch.setenv("STREAM_KERNEL_PROCESS_GROUP", "execution.ingress")
    monkeypatch.setenv("STREAM_KERNEL_WORKER_ID", "execution.ingress#1")
    registry = InjectionRegistry()
    pipeline = _Pipeline()
    service = _Service()
    registry.register_factory("service", ObservabilityPipelineService, lambda: pipeline)
    registry.register_factory("service", _Service, lambda: service)
    scope = registry.instantiate_for_scenario("s1")

    class _Holder:
        dep = inject.service(_Service)

    holder = _Holder()
    _apply_injection(holder, scope, strict=True)
    assert isinstance(holder.dep, _Service)
    assert holder.dep is not service
    assert holder.dep.ping({"x": 1}) == "pong"
    assert service.calls == [{"x": 1}]
    assert len(pipeline.records) == 1
    message, trace_id, attrs = pipeline.records[0]
    assert trace_id is None
    assert attrs == {"channel": "runtime_debug"}
    assert message.fields.get("event") == "runtime.inject.port_call"
    assert message.fields.get("port_type") == "service"
    assert message.fields.get("method") == "ping"
    assert isinstance(message.fields.get("caller_function"), str)


def test_inject_debug_does_not_wrap_logmessage_stream_ports(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    registry = InjectionRegistry()
    pipeline = _Pipeline()
    log_port = _LogPort()
    registry.register_factory("stream", LogMessage, lambda: log_port)
    registry.register_factory("service", ObservabilityPipelineService, lambda: pipeline)
    scope = registry.instantiate_for_scenario("s1")

    class _Holder:
        dep = inject.stream(LogMessage)

    holder = _Holder()
    _apply_injection(holder, scope, strict=True)
    assert holder.dep is log_port
    assert holder.dep.write("x") == "written"
    assert log_port.calls == ["x"]
    assert pipeline.records == []


def test_inject_debug_suppresses_empty_poll_calls(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    registry = InjectionRegistry()
    pipeline = _Pipeline()
    poll_port = _PollingPort()
    registry.register_factory("ipc", _Token, lambda: poll_port)
    registry.register_factory("service", ObservabilityPipelineService, lambda: pipeline)
    scope = registry.instantiate_for_scenario("s1")

    class _Holder:
        dep = inject.ipc(_Token)

    holder = _Holder()
    _apply_injection(holder, scope, strict=True)
    assert holder.dep.recv(timeout=0.01) is None
    assert pipeline.records == []
