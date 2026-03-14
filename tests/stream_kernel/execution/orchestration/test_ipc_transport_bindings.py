from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration import builder as execution_builder
from stream_kernel.execution.transport.ipc import (
    CreditWindowFlowControlPolicy,
    ExecutionIpcPort,
    ExecutionIpcKvStreamPort,
    ExecutionIpcTransportService,
    ExecutionIpcTransportCoordinatorService,
    InMemoryExecutionIpcTransportAdapter,
    NoopFlowControlPolicy,
    PipeExecutionIpcTransportAdapter,
)


def _runtime_with_groups(*names: str) -> dict[str, object]:
    return {
        "platform": {
            "bootstrap": {"mode": "inline"},
            "process_groups": [{"name": name, "workers": 1} for name in names],
        }
    }


def test_runtime_ipc_bindings_register_group_ports() -> None:
    runtime = _runtime_with_groups("alpha", "beta")
    registry = InjectionRegistry()
    execution_builder.ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("run")

    sender = scope.resolve("ipc", ExecutionIpcPort)
    receiver = scope.resolve("ipc", ExecutionIpcPort, qualifier="alpha")

    sender.send("group:alpha", {"value": 1})
    message = receiver.recv(timeout=0.1)
    assert message is not None
    assert message.payload == {"value": 1}


def test_runtime_ipc_bindings_control_port_is_unbuffered() -> None:
    runtime = _runtime_with_groups("alpha")
    registry = InjectionRegistry()
    execution_builder.ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("run")

    sender = scope.resolve("ipc", ExecutionIpcPort)
    control = scope.resolve("ipc", ExecutionIpcPort, qualifier="control")

    sender.send("control", "a")
    sender.send("control", "b")

    first = control.recv(timeout=0.1)
    second = control.recv(timeout=0.1)
    assert first is not None
    assert second is not None
    assert first.payload == "a"
    assert second.payload == "b"


def test_runtime_ipc_bindings_respects_group_buffer_overrides() -> None:
    runtime = {
        "platform": {
            "bootstrap": {"mode": "inline"},
            "process_groups": [{"name": "alpha", "workers": 1}],
            "execution_ipc": {
                "transport": "tcp_local",
                "auth": {"mode": "hmac"},
                "buffer": {
                    "batch_max_items": 3,
                    "flush_interval_ms": 1000,
                    "per_group": {"alpha": {"batch_max_items": 2}},
                },
            },
        }
    }
    registry = InjectionRegistry()
    execution_builder.ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("run")
    sender = scope.resolve("ipc", ExecutionIpcPort)
    receiver = scope.resolve("ipc", ExecutionIpcPort, qualifier="alpha")

    sender.send("group:alpha", "a")
    sender.send("group:alpha", "b")

    first = receiver.recv(timeout=0.1)
    second = receiver.recv(timeout=0.1)
    assert first is not None
    assert second is not None
    assert first.payload == "a"
    assert second.payload == "b"


def test_runtime_ipc_bindings_uses_provided_service_instance() -> None:
    runtime = _runtime_with_groups("alpha")
    registry = InjectionRegistry()
    adapter = InMemoryExecutionIpcTransportAdapter()
    service = ExecutionIpcTransportCoordinatorService(adapter=adapter)
    execution_builder.ensure_runtime_ipc_bindings(
        injection_registry=registry,
        runtime=runtime,
        service=service,
    )
    scope = registry.instantiate_for_scenario("run")
    sender = scope.resolve("ipc", ExecutionIpcPort)
    receiver = scope.resolve("ipc", ExecutionIpcPort, qualifier="alpha")

    assert sender._service is service
    assert receiver._service is service


def test_runtime_ipc_service_resolves_from_adapter_bindings() -> None:
    adapter = InMemoryExecutionIpcTransportAdapter()
    adapter_instances = {"ipc_transport": adapter}
    adapter_bindings = {"ipc_transport": [("kv_stream", ExecutionIpcKvStreamPort)]}

    resolved = execution_builder.resolve_execution_ipc_adapter_from_adapters(
        adapter_bindings=adapter_bindings,
        adapter_instances=adapter_instances,
    )
    assert resolved is adapter


def test_runtime_ipc_bindings_apply_flow_control_defaults_to_noop() -> None:
    runtime = {
        "platform": {
            "process_groups": [{"name": "alpha", "workers": 1}],
            "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        }
    }
    registry = InjectionRegistry()
    execution_builder.ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("run")
    service = scope.resolve("service", ExecutionIpcTransportService)
    assert isinstance(service, ExecutionIpcTransportCoordinatorService)
    assert isinstance(service.flow_control, NoopFlowControlPolicy)


def test_runtime_ipc_bindings_apply_flow_control_credits_when_configured() -> None:
    runtime = {
        "platform": {
            "process_groups": [{"name": "alpha", "workers": 1}],
            "execution_ipc": {
                "transport": "tcp_local",
                "auth": {"mode": "hmac"},
                "flow_control": {
                    "mode": "credits",
                    "credits": {"window_size": 32},
                },
            },
        }
    }
    registry = InjectionRegistry()
    execution_builder.ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("run")
    service = scope.resolve("service", ExecutionIpcTransportService)
    assert isinstance(service, ExecutionIpcTransportCoordinatorService)
    assert isinstance(service.flow_control, CreditWindowFlowControlPolicy)


def test_runtime_ipc_bindings_apply_polling_settings() -> None:
    runtime = {
        "platform": {
            "process_groups": [{"name": "alpha", "workers": 1}],
            "execution_ipc": {
                "transport": "tcp_local",
                "auth": {"mode": "hmac"},
                "poll_mode": "auto",
                "poll_interval_ms": 3,
            },
        }
    }
    registry = InjectionRegistry()
    adapter = PipeExecutionIpcTransportAdapter()
    service = ExecutionIpcTransportCoordinatorService(adapter=adapter)
    execution_builder.ensure_runtime_ipc_bindings(
        injection_registry=registry,
        runtime=runtime,
        service=service,
    )
    assert adapter._poll_mode == "auto"
    assert abs(adapter._poll_interval_seconds - 0.003) < 1e-6
