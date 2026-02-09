from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import (
    _build_adapter_bindings,
    _build_adapter_instances_from_registry,
    _build_injection_registry_from_bindings,
    _ensure_platform_discovery_modules,
    _load_discovery_modules,
    _initial_context,
    _register_discovered_services,
    _resolve_runtime_adapters,
    _scenario_name,
    _ensure_runtime_kv_binding,
    _trace_id,
)
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.context import ContextService
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.observer_builder import build_execution_observers_from_factories
from stream_kernel.execution.observer import ExecutionObserver, ObserverFactoryContext
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


class _Token:
    pass


class _OtherToken:
    pass


class _StreamPort:
    pass


class _KvPort:
    pass


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@adapter(name="source", kind="test.source", consumes=[], emits=[_Token], binds=[("stream", _StreamPort)])
def _source_factory(settings: dict[str, object]) -> object:
    return object()


def test_resolve_runtime_adapters_requires_mapping_role_config() -> None:
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"sink": "nope"}, discovery_modules=[])  # type: ignore[arg-type]


def test_resolve_runtime_adapters_rejects_kind_in_yaml() -> None:
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"sink": {"kind": "legacy"}}, discovery_modules=[])


def test_resolve_runtime_adapters_rejects_unknown_adapter_name(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("fake.adapters")
    module.source = _source_factory
    monkeypatch.setattr("stream_kernel.app.runtime.importlib.import_module", lambda _name: module)
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"missing": {}}, discovery_modules=["fake.adapters"])


def test_build_adapter_bindings_requires_supported_port_type() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    with pytest.raises(ValueError):
        _build_adapter_bindings(
            {"source": {"binds": ["kv"]}},
            registry,
        )


def test_build_adapter_bindings_resolves_typed_ports() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    bindings = _build_adapter_bindings(
        {"source": {"binds": ["stream"]}},
        registry,
    )
    assert bindings["source"] == [("stream", _StreamPort)]


def test_build_adapter_instances_from_registry_requires_mapping() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        _build_adapter_instances_from_registry({"source": "nope"}, registry)  # type: ignore[arg-type]


def test_build_injection_registry_from_bindings_requires_instance() -> None:
    with pytest.raises(ValueError):
        _build_injection_registry_from_bindings({}, {"source": [("stream", _StreamPort)]})


def test_ensure_platform_discovery_modules_appends_framework_modules() -> None:
    modules = ["fund_load.usecases.steps"]
    _ensure_platform_discovery_modules(modules)
    assert "stream_kernel.observability.adapters" in modules
    assert "stream_kernel.observability.observers" in modules


def test_ensure_platform_discovery_modules_does_not_duplicate_entries() -> None:
    modules = [
        "fund_load.usecases.steps",
        "stream_kernel.observability.adapters",
        "stream_kernel.observability.observers",
    ]
    _ensure_platform_discovery_modules(modules)
    assert modules.count("stream_kernel.observability.adapters") == 1
    assert modules.count("stream_kernel.observability.observers") == 1


def test_load_discovery_modules_expands_package_root_recursively(
    tmp_path: Path,
) -> None:
    # Root package names should expand to all importable submodules recursively.
    pkg = tmp_path / "fake_root"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "mod_a.py", "x = 1\n")
    _write_file(pkg / "nested" / "__init__.py", "")
    _write_file(pkg / "nested" / "mod_b.py", "y = 2\n")

    sys.path.insert(0, str(tmp_path))
    try:
        modules = _load_discovery_modules(["fake_root"])
    finally:
        sys.path.remove(str(tmp_path))

    names = [m.__name__ for m in modules]
    assert "fake_root" not in names
    assert "fake_root.mod_a" in names
    assert "fake_root.nested" in names
    assert "fake_root.nested.mod_b" in names


def test_load_discovery_modules_deduplicates_root_and_explicit_submodule(
    tmp_path: Path,
) -> None:
    # If both root and submodule are declared, each module should still appear once.
    pkg = tmp_path / "fake_root_dupe"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "mod_a.py", "x = 1\n")

    sys.path.insert(0, str(tmp_path))
    try:
        modules = _load_discovery_modules(["fake_root_dupe", "fake_root_dupe.mod_a"])
    finally:
        sys.path.remove(str(tmp_path))

    names = [m.__name__ for m in modules]
    assert names.count("fake_root_dupe") == 0
    assert names.count("fake_root_dupe.mod_a") == 1


class _Observer:
    def before_node(self, **kwargs: object) -> object | None:
        return None

    def after_node(self, **kwargs: object) -> None:
        return None

    def on_node_error(self, **kwargs: object) -> None:
        return None

    def on_run_end(self) -> None:
        return None


def test_build_execution_observers_collects_from_factories() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> ExecutionObserver:
        return _Observer()

    observers = build_execution_observers_from_factories(
        factories={"x": _factory},
        runtime={},
        adapter_instances={},
        run_id="r1",
        scenario_id="s1",
        step_specs=[],
    )
    assert len(observers) == 1


def test_build_execution_observers_flattens_list_result() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> list[ExecutionObserver]:
        return [_Observer(), _Observer()]

    observers = build_execution_observers_from_factories(
        factories={"x": _factory},
        runtime={},
        adapter_instances={},
        run_id="r1",
        scenario_id="s1",
        step_specs=[],
    )
    assert len(observers) == 2


def test_build_execution_observers_rejects_non_observer_result() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> object:
        return object()

    with pytest.raises(ValueError):
        build_execution_observers_from_factories(
            factories={"x": _factory},
            runtime={},
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            step_specs=[],
        )


def test_scenario_name_falls_back_when_missing() -> None:
    assert _scenario_name({"scenario": "nope"}) == "scenario"


def test_trace_id_is_index_based_even_for_payload_with_line_no() -> None:
    class _Payload:
        line_no = 42

    # Runtime trace identity must be framework-generic and not depend on project fields.
    assert _trace_id("run", _Payload(), 7) == "run:7"


def test_initial_context_does_not_include_payload_line_no() -> None:
    class _Payload:
        line_no = 42

    ctx = _initial_context(_Payload(), "run:7", run_id="run", scenario_id="s")
    assert ctx == {
        "__trace_id": "run:7",
        "__run_id": "run",
        "__scenario_id": "s",
    }


def test_register_discovered_services_registers_service_contracts() -> None:
    # Runtime should register services discovered via @service markers.
    module = ModuleType("fake.services")

    @service(name="ctx")
    class _CustomContextService(ContextService):
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module._CustomContextService = _CustomContextService

    registry = InjectionRegistry()
    _register_discovered_services(registry, [module])
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", ContextService)
    assert isinstance(resolved, _CustomContextService)


def test_register_discovered_services_keeps_existing_binding() -> None:
    # User/platform override must not be replaced by auto-discovered defaults.
    class _CustomContextService:
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module = ModuleType("fake.services")

    @service(name="ctx")
    class _DiscoveredContextService(ContextService):
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module._DiscoveredContextService = _DiscoveredContextService

    registry = InjectionRegistry()
    custom = _CustomContextService()
    registry.register_factory("service", ContextService, lambda: custom)
    _register_discovered_services(registry, [module])
    scope = registry.instantiate_for_scenario("s1")
    assert scope.resolve("service", ContextService) is custom


def test_ensure_runtime_kv_binding_registers_memory_backend_when_missing() -> None:
    # Runtime must provision default KV binding so services can inject KVStore without extra YAML boilerplate.
    registry = InjectionRegistry()
    _ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "memory"}}})

    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("kv", KVStore)
    assert isinstance(resolved, InMemoryKvStore)


def test_ensure_runtime_kv_binding_keeps_existing_binding() -> None:
    # Explicit bindings win over runtime defaults.
    registry = InjectionRegistry()
    custom = object()
    registry.register_factory("kv", KVStore, lambda: custom)

    _ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "memory"}}})
    scope = registry.instantiate_for_scenario("s1")
    assert scope.resolve("kv", KVStore) is custom


def test_ensure_runtime_kv_binding_rejects_unknown_backend() -> None:
    # Unsupported backend must fail fast during runtime bootstrap.
    registry = InjectionRegistry()
    with pytest.raises(ValueError):
        _ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "redis"}}})
