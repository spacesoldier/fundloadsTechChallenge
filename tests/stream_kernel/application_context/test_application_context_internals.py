from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.application_context.config_inject import ConfigScope, config
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.kernel.node import node

# Internal helpers are exercised to raise coverage for application context wiring semantics.
from stream_kernel.application_context.application_context import (
    _apply_config,
    _apply_injection,
    _build_config_scope,
    _collect_config_from_object,
    _resolve_config,
)


def test_resolve_config_accepts_dict_method() -> None:
    # Config models with dict() should be accepted (docs/framework/initial_stage/Injection and strict mode.md).
    class _Config:
        def dict(self) -> dict[str, object]:
            return {"nodes": {"a": {"flag": True}}, "global": {}}

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        flag: object = config.value("flag", default=False)

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod = types.ModuleType("cfg_nodes")
    mod.A = A
    ctx = ApplicationContext()
    ctx.discover([mod])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={"config": _Config(), "strict": True},
    )
    assert scenario.steps[0].step.flag is True


def test_build_config_scope_rejects_invalid_mappings() -> None:
    # Node/global slices must be mappings (Configuration spec §2.1).
    with pytest.raises(ContextBuildError):
        _build_config_scope({"nodes": "nope", "global": {}}, "a")
    with pytest.raises(ContextBuildError):
        _build_config_scope({"nodes": {}, "global": "nope"}, "a")
    with pytest.raises(ContextBuildError):
        _build_config_scope({"nodes": {"a": "nope"}, "global": {}}, "a")


def test_collect_config_from_object_reads_instance_and_class() -> None:
    # ConfigValue descriptors can live on instances or classes (Factory/injection model doc).
    class _Obj:
        cfg_class = config.value("x", default=1)

        def __init__(self) -> None:
            self.cfg_inst = config.value("y", default=2)

    collected = _collect_config_from_object(_Obj())
    assert {c.path for c in collected} == {"x", "y"}


def test_apply_injection_populates_instance_and_class_fields() -> None:
    # @inject should populate both instance and class-level markers (Injection registry spec).
    registry = InjectionRegistry()
    registry.register_factory("kv", str, lambda: "ok")
    scope = registry.instantiate_for_scenario("s1")

    class _Obj:
        dep_class = inject.kv(str)

        def __init__(self) -> None:
            self.dep_inst = inject.kv(str)

    obj = _Obj()
    _apply_injection(obj, scope, strict=True)

    assert obj.dep_class == "ok"
    assert obj.dep_inst == "ok"


def test_apply_injection_bound_method_strict_raises_on_missing_dep() -> None:
    # Missing injection should raise in strict mode (Injection and strict mode doc).
    registry = InjectionRegistry()
    scope = registry.instantiate_for_scenario("s1")

    class _Container:
        dep = inject.kv(str)

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    method = _Container().work
    with pytest.raises(ContextBuildError):
        _apply_injection(method, scope, strict=True)


def test_apply_injection_bound_method_non_strict_sets_none() -> None:
    # Non-strict mode should set missing injections to None (Injection and strict mode doc).
    registry = InjectionRegistry()
    scope = registry.instantiate_for_scenario("s1")

    class _Container:
        dep = inject.kv(str)

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    container = _Container()
    method = container.work
    _apply_injection(method, scope, strict=False)

    assert container.dep is None


def test_apply_config_strict_missing_raises() -> None:
    # Strict config mode rejects missing fields (Injection and strict mode doc).
    class _Obj:
        cfg = config.value("missing")

    obj = _Obj()
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={})
    with pytest.raises(ContextBuildError):
        _apply_config(obj, scope, strict=True)


def test_apply_config_non_strict_global_fallback_for_container() -> None:
    # Non-strict mode allows global.* fallback to root config (Injection and strict mode doc).
    class _Container:
        cfg = config.value("global.shared")

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    container = _Container()
    method = container.work
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={"shared": "ok"})
    _apply_config(method, scope, strict=False)

    assert container.cfg == "ok"


def test_resolve_config_rejects_non_mapping() -> None:
    # Config objects that are not mappings (and lack dict/model_dump) should be rejected.
    with pytest.raises(ContextBuildError):
        _resolve_config({"config": 1})


def test_apply_config_reads_instance_fields_and_handles_missing_fallback() -> None:
    # Instance-level ConfigValue markers should be discovered and resolved (Injection registry doc).
    class _Obj:
        def __init__(self) -> None:
            self.cfg = config.value("global.missing")

    obj = _Obj()
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={})
    _apply_config(obj, scope, strict=False)

    assert obj.cfg is None


def test_apply_config_bound_method_missing_fallback_sets_none() -> None:
    # Non-strict mode should allow missing global.* values to resolve to None (Injection and strict mode doc).
    class _Container:
        cfg = config.value("global.missing")

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    container = _Container()
    method = container.work
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={})
    _apply_config(method, scope, strict=False)

    assert container.cfg is None


def test_apply_config_bound_method_strict_missing_raises() -> None:
    # Strict mode should raise when container config is missing (Injection and strict mode doc).
    class _Container:
        cfg = config.value("missing")

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    container = _Container()
    method = container.work
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={})
    with pytest.raises(ContextBuildError):
        _apply_config(method, scope, strict=True)


def test_apply_config_bound_method_non_strict_missing_non_global_sets_none() -> None:
    # Non-strict mode should set missing non-global config values to None (Injection and strict mode doc).
    class _Container:
        cfg = config.value("missing")

        def work(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    container = _Container()
    method = container.work
    scope = ConfigScope(node_cfg={}, global_cfg={}, root_cfg={})
    _apply_config(method, scope, strict=False)

    assert container.cfg is None
