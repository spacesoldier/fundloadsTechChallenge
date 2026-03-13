from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
    ChildBootstrapBundle,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service import (
    DefaultLeafRuntimeBootstrapService,
    LeafRuntimeBootstrapAssemblyResult,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service import (
    LeafRuntimeStepAssemblyResult,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService


def _runtime_tcp_local_generated() -> dict[str, object]:
    return {
        "strict": True,
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {
                    "mode": "hmac",
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                    "ttl_seconds": 30,
                    "nonce_cache_size": 1000,
                },
                "max_payload_bytes": 1048576,
            },
            "process_groups": [
                {"name": "execution.cpu", "nodes": ["app.node"]},
            ],
        },
        "observability": {},
    }


class _StubRuntimeTransport(RuntimeTransportService):
    profile = "stub"

    def build_queue(self):  # pragma: no cover - not used in this contract test
        raise NotImplementedError

    def build_topic(self):  # pragma: no cover - not used in this contract test
        raise NotImplementedError


class _StubRuntimeLifecycle(RuntimeLifecycleManager):
    def start(self) -> None:  # pragma: no cover - not used in this contract test
        return None

    def ready(self, timeout_seconds: int) -> bool:  # pragma: no cover - not used here
        _ = timeout_seconds
        return True

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:  # pragma: no cover
        _ = (graceful_timeout_seconds, drain_inflight)
        return None


@dataclass(slots=True)
class _StubScope:
    runtime_transport: RuntimeTransportService
    runtime_lifecycle: RuntimeLifecycleManager

    def resolve(self, kind: str, contract: object) -> object:
        if kind != "service":
            raise RuntimeError(f"unexpected resolve kind: {kind}")
        if contract is RuntimeTransportService:
            return self.runtime_transport
        if contract is RuntimeLifecycleManager:
            return self.runtime_lifecycle
        raise RuntimeError(f"unexpected contract: {contract}")


def test_leaf_runtime_bootstrap_service_delegates_to_injected_services(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service as mod

    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"x" * n, now_fn=lambda: 1)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_contract",
        process_group="execution.cpu",
        discovery_modules=["fund_load"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    runtime_transport = _StubRuntimeTransport()
    runtime_lifecycle = _StubRuntimeLifecycle()
    scope = _StubScope(runtime_transport=runtime_transport, runtime_lifecycle=runtime_lifecycle)
    injection_registry = InjectionRegistry()
    app_context = ApplicationContext()
    sentinel_step = object()

    class _AssemblyStub:
        def __init__(self) -> None:
            self.calls = 0

        def assemble(self, *, bundle_typed: ChildBootstrapBundle) -> LeafRuntimeBootstrapAssemblyResult:
            self.calls += 1
            assert bundle_typed is bundle
            return LeafRuntimeBootstrapAssemblyResult(
                bundle=bundle,
                discovery_modules=["fund_load"],
                modules=[],
                app_context=app_context,
                injection_registry=injection_registry,
                adapter_registry=None,
                adapter_instances={},
                scenario_scope=scope,
                scenario=object(),
                step_names=["app.node"],
            )

    class _StepAssemblyStub:
        def __init__(self) -> None:
            self.calls = 0

        def assemble_steps(self, **kwargs: object) -> LeafRuntimeStepAssemblyResult:
            self.calls += 1
            assert kwargs["bundle"] is bundle
            assert kwargs["scenario_scope"] is scope
            assert "execution_builder" not in kwargs
            assert "consumer_registry" not in kwargs
            return LeafRuntimeStepAssemblyResult(
                scenario_steps={"app.node": sentinel_step},
                full_context_nodes={"system.cp.node"},
            )

    assembly = _AssemblyStub()
    step_assembly = _StepAssemblyStub()

    monkeypatch.setattr(mod, "plan_pools", lambda *_args, **_kwargs: {"app.node": "sync"})
    monkeypatch.setattr(mod, "classify_async_bindings", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(mod, "collect_observability_exporter_summary", lambda **_kwargs: [])

    service = DefaultLeafRuntimeBootstrapService(
        assembly_service=assembly,
        step_assembly_service=step_assembly,
    )
    result = service.bootstrap_runtime(bundle=bundle)

    assert assembly.calls == 1
    assert step_assembly.calls == 1
    assert result.scenario_id == "scenario_contract"
    assert result.scenario_steps["app.node"] is sentinel_step
    assert result.runtime_transport is runtime_transport
    assert result.runtime_lifecycle is runtime_lifecycle
    assert result.runner_profile_effective == "async"


def test_leaf_runtime_bootstrap_service_uses_default_fallback_services(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service as mod

    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"y" * n, now_fn=lambda: 2)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_fallback",
        process_group="execution.cpu",
        discovery_modules=["fund_load"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    runtime_transport = _StubRuntimeTransport()
    runtime_lifecycle = _StubRuntimeLifecycle()
    scope = _StubScope(runtime_transport=runtime_transport, runtime_lifecycle=runtime_lifecycle)
    injection_registry = InjectionRegistry()

    class _FallbackAssembly:
        def __init__(self) -> None:
            self.calls = 0

        def assemble(self, *, bundle_typed: ChildBootstrapBundle) -> LeafRuntimeBootstrapAssemblyResult:
            self.calls += 1
            assert bundle_typed is bundle
            return LeafRuntimeBootstrapAssemblyResult(
                bundle=bundle,
                discovery_modules=["fund_load"],
                modules=[],
                app_context=ApplicationContext(),
                injection_registry=injection_registry,
                adapter_registry=None,
                adapter_instances={},
                scenario_scope=scope,
                scenario=object(),
                step_names=["app.node"],
            )

    class _FallbackStepAssembly:
        def __init__(self) -> None:
            self.calls = 0

        def assemble_steps(self, **_kwargs: object) -> LeafRuntimeStepAssemblyResult:
            self.calls += 1
            return LeafRuntimeStepAssemblyResult(
                scenario_steps={"app.node": object()},
                full_context_nodes=set(),
            )

    fallback_assembly = _FallbackAssembly()
    fallback_step_assembly = _FallbackStepAssembly()

    monkeypatch.setattr(mod, "DefaultLeafRuntimeBootstrapAssemblyService", lambda: fallback_assembly)
    monkeypatch.setattr(mod, "DefaultLeafRuntimeStepAssemblyService", lambda: fallback_step_assembly)
    monkeypatch.setattr(mod, "plan_pools", lambda *_args, **_kwargs: {"app.node": "sync"})
    monkeypatch.setattr(mod, "classify_async_bindings", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(mod, "collect_observability_exporter_summary", lambda **_kwargs: [])

    service = DefaultLeafRuntimeBootstrapService(
        assembly_service=object(),
        step_assembly_service=object(),
    )
    _ = service.bootstrap_runtime(bundle=bundle)

    assert fallback_assembly.calls == 1
    assert fallback_step_assembly.calls == 1
