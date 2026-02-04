from __future__ import annotations

import importlib
import os
import pkgutil
from dataclasses import dataclass, field, fields, is_dataclass
from types import ModuleType

from stream_kernel.application_context.config_inject import ConfigScope, ConfigValue
from stream_kernel.application_context.inject import Injected
from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)
from stream_kernel.kernel.discovery import discover_nodes
from stream_kernel.kernel.node import NodeDef
from stream_kernel.kernel.scenario import Scenario
from stream_kernel.kernel.scenario_builder import InvalidScenarioConfigError, ScenarioBuilder
from stream_kernel.kernel.stage import StageDef
from stream_kernel.kernel.step_registry import StepRegistry, UnknownStepError


class ContextBuildError(RuntimeError):
    # Raised when the application context cannot validate dependencies.
    pass


@dataclass(slots=True)
class ApplicationContext:
    # Holds discovered node definitions and validates dependency references.
    nodes: list[NodeDef] = field(default_factory=list)

    def discover(self, modules: list[ModuleType]) -> None:
        # Discover nodes and store them for later wiring.
        self.nodes = discover_nodes(modules)

    def auto_discover(self) -> None:
        # Auto-discover modules under the configured root package.
        root = os.getenv("APP_CONTEXT_ROOT")
        if not root:
            raise ContextBuildError("APP_CONTEXT_ROOT is not set")
        try:
            root_pkg = importlib.import_module(root)
        except Exception as exc:  # noqa: BLE001 - wrap with explicit error
            raise ContextBuildError(f"Failed to import root package: {root}") from exc

        if not hasattr(root_pkg, "__path__"):
            raise ContextBuildError(f"Root package has no __path__: {root}")

        exclude_raw = os.getenv("APP_CONTEXT_EXCLUDE", "")
        exclude = [item.strip() for item in exclude_raw.split(",") if item.strip()]

        def _excluded(module_name: str) -> bool:
            return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in exclude)

        modules: list[ModuleType] = [root_pkg]
        for module_info in pkgutil.walk_packages(root_pkg.__path__, prefix=f"{root}."):
            if _excluded(module_info.name):
                continue
            try:
                modules.append(importlib.import_module(module_info.name))
            except Exception as exc:  # noqa: BLE001 - skip or fail? we fail fast for now
                raise ContextBuildError(f"Failed to import module: {module_info.name}") from exc

        self.discover(modules)

    def validate_dependencies(self) -> None:
        # Ensure that all declared requires are provided by some node name.
        names = {n.meta.name for n in self.nodes}
        missing: set[str] = set()
        for node in self.nodes:
            for required in node.meta.requires:
                if required not in names:
                    missing.add(required)
        if missing:
            raise ContextBuildError(f"Missing node dependencies: {sorted(missing)}")

    def build_registry(self) -> StepRegistry:
        # Build a StepRegistry from discovered nodes (ApplicationContext spec).
        self.validate_dependencies()
        registry = StepRegistry()

        for discovered in self.nodes:
            target = discovered.target
            container_cls = discovered.container_cls
            container_attr = discovered.container_attr

            def _factory(cfg: dict[str, object], wiring: dict[str, object], *, _t=target):  # type: ignore[override]
                # Default factory: instantiate classes with no args or treat functions as factories.
                if isinstance(_t, type):
                    return _t()
                # Function nodes: if callable with cfg, treat as factory; otherwise treat as step.
                try:
                    step = _t(cfg) # pyright: ignore[reportCallIssue]
                except TypeError:
                    return _t
                if not callable(step):
                    raise ContextBuildError("Function node factory must return a callable step")
                return step

            if container_cls is not None and container_attr is not None:
                def _factory(cfg: dict[str, object], wiring: dict[str, object], *, _c=container_cls, _a=container_attr):  # type: ignore[override]
                    # Method nodes: instantiate container per scenario and bind method.
                    instance = _c()
                    return getattr(instance, _a)

            registry.register(discovered.meta.name, _factory)

        return registry

    def build_scenario(
        self,
        *,
        scenario_id: str,
        step_names: list[str],
        wiring: object,
    ) -> Scenario:
        # Build a Scenario from a list of step names, preserving order.
        registry = self.build_registry()
        builder = ScenarioBuilder(registry=registry)
        steps_cfg = [{"name": name, "config": {}} for name in step_names]
        try:
            scenario = builder.build(scenario_id=scenario_id, steps=steps_cfg, wiring=_as_wiring_dict(wiring))
        except (UnknownStepError, InvalidScenarioConfigError) as exc:
            raise ContextBuildError(str(exc)) from exc

        # Resolve @inject fields using scenario-scoped registry.
        scope = _resolve_scope(wiring, scenario_id)
        strict = _resolve_strict(wiring)
        cfg = _resolve_config(wiring)
        if scope is not None:
            for step in scenario.steps:
                _apply_injection(step.step, scope, strict)
        if cfg is not None:
            self.validate_config_requirements(cfg, strict=strict)
            for step in scenario.steps:
                scope_cfg = _build_config_scope(cfg, step.name)
                _apply_config(step.step, scope_cfg, strict)

        return scenario

    def group_by_stage(self, *, stage_overrides: dict[str, str] | None = None) -> list[StageDef]:
        # Group nodes by stage for diagnostics or future deployment planning.
        overrides = stage_overrides or {}
        grouped: dict[str, list[NodeDef]] = {}
        for node in self.nodes:
            # If stage is not explicitly set, infer it from the declaring symbol name.
            inferred_stage = node.meta.stage or getattr(node.target, "__name__", "")
            stage = overrides.get(node.meta.name, inferred_stage)
            grouped.setdefault(stage, []).append(node)
        return [StageDef(name=stage_name, nodes=nodes) for stage_name, nodes in grouped.items()]

    def config_requirements(self) -> dict[str, list[ConfigValue]]:
        # Collect config fields requested by each node (ConfigValue descriptors).
        requirements: dict[str, list[ConfigValue]] = {}
        for node_def in self.nodes:
            required = _collect_config_fields(node_def)
            requirements[node_def.meta.name] = required
        return requirements

    def validate_config_requirements(self, config: dict[str, object], *, strict: bool = True) -> None:
        # Validate that required config paths exist for each node.
        for node_def in self.nodes:
            scope_cfg = _build_config_scope(config, node_def.meta.name)
            for cfg_value in _collect_config_fields(node_def):
                if cfg_value.has_default:
                    continue
                try:
                    cfg_value.resolve(scope_cfg)
                except KeyError as exc:
                    if strict:
                        raise ContextBuildError(
                            f"Missing config for node '{node_def.meta.name}': {cfg_value.path}"
                        ) from exc


def _as_wiring_dict(wiring: object) -> dict[str, object]:
    # Allow either dict wiring or simple objects with attributes.
    if isinstance(wiring, dict):
        return wiring
    return wiring.__dict__


def _resolve_scope(wiring: object, scenario_id: str):
    # Resolve a scenario-scoped registry if present.
    registry = None
    if isinstance(wiring, dict):
        registry = wiring.get("injection_registry")
    else:
        registry = getattr(wiring, "injection_registry", None)
    if isinstance(registry, InjectionRegistry):
        return registry.instantiate_for_scenario(scenario_id)
    return None


def _resolve_strict(wiring: object) -> bool:
    if isinstance(wiring, dict):
        return bool(wiring.get("strict", True))
    return bool(getattr(wiring, "strict", True))


def _resolve_config(wiring: object) -> dict[str, object] | None:
    if isinstance(wiring, dict):
        cfg = wiring.get("config")
    else:
        cfg = getattr(wiring, "config", None)
    if cfg is None:
        return None
    # Accept config models that expose model_dump()/dict() (e.g., Pydantic) to avoid
    # hard-coding project-specific AppConfig types in the framework.
    if not isinstance(cfg, dict):
        if hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()  # type: ignore[assignment]
        elif hasattr(cfg, "dict"):
            cfg = cfg.dict()  # type: ignore[assignment]
    if not isinstance(cfg, dict):
        raise ContextBuildError("config must be a mapping when provided")
    return cfg


def _build_config_scope(cfg: dict[str, object], node_name: str) -> ConfigScope:
    nodes = cfg.get("nodes", {})
    global_cfg = cfg.get("global", {})
    if not isinstance(nodes, dict):
        raise ContextBuildError("config.nodes must be a mapping when provided")
    if not isinstance(global_cfg, dict):
        raise ContextBuildError("config.global must be a mapping when provided")
    node_cfg = nodes.get(node_name, {})
    if not isinstance(node_cfg, dict):
        raise ContextBuildError(f"config.nodes.{node_name} must be a mapping when provided")
    # If no explicit global section, allow top-level config as global fallback.
    if not global_cfg:
        global_cfg = cfg
    return ConfigScope(node_cfg=node_cfg, global_cfg=global_cfg, root_cfg=cfg)


def _iter_injected_fields(obj: object):
    if is_dataclass(obj):
        for f in fields(obj):
            value = getattr(obj, f.name)
            if isinstance(value, Injected):
                yield f.name, value
    else:
        for name, value in getattr(obj, "__dict__", {}).items():
            if isinstance(value, Injected):
                yield name, value
        # Also scan class attributes for Injected markers.
        for name, value in getattr(obj.__class__, "__dict__", {}).items():
            if isinstance(value, Injected):
                yield name, value


def _iter_config_fields(obj: object):
    if is_dataclass(obj):
        for f in fields(obj):
            value = getattr(obj, f.name)
            if isinstance(value, ConfigValue):
                yield f.name, value
    else:
        for name, value in getattr(obj, "__dict__", {}).items():
            if isinstance(value, ConfigValue):
                yield name, value
        # Also scan class attributes for ConfigValue markers.
        for name, value in getattr(obj.__class__, "__dict__", {}).items():
            if isinstance(value, ConfigValue):
                yield name, value


def _collect_config_fields(node_def: NodeDef) -> list[ConfigValue]:
    # Gather ConfigValue descriptors from node targets or container classes.
    target = node_def.target
    container_cls = node_def.container_cls

    if container_cls is not None:
        return _collect_config_from_type(container_cls)

    if isinstance(target, type):
        return _collect_config_from_type(target)

    # Function nodes can expose ConfigValue as attributes.
    return _collect_config_from_object(target)


def _collect_config_from_type(cls: type[object]) -> list[ConfigValue]:
    collected: list[ConfigValue] = []

    if hasattr(cls, "__dataclass_fields__"):
        for field_info in getattr(cls, "__dataclass_fields__", {}).values():
            if isinstance(field_info.default, ConfigValue):
                collected.append(field_info.default)
            elif field_info.default_factory is not None:  # pragma: no cover - defensive path
                try:
                    candidate = field_info.default_factory()
                except Exception:
                    continue
                if isinstance(candidate, ConfigValue):
                    collected.append(candidate)

    for value in getattr(cls, "__dict__", {}).values():
        if isinstance(value, ConfigValue):
            collected.append(value)

    return collected


def _collect_config_from_object(obj: object) -> list[ConfigValue]:
    collected: list[ConfigValue] = []
    for value in getattr(obj, "__dict__", {}).values():
        if isinstance(value, ConfigValue):
            collected.append(value)
    for value in getattr(obj.__class__, "__dict__", {}).values():
        if isinstance(value, ConfigValue):
            collected.append(value)
    return collected


def _apply_injection(obj: object, scope, strict: bool) -> None:
    for name, injected in _iter_injected_fields(obj):
        try:
            resolved = injected.resolve(scope)
        except InjectionRegistryError as exc:
            if strict:
                raise ContextBuildError(str(exc)) from exc
            resolved = None
        # Bypass frozen/slots via object.__setattr__.
        object.__setattr__(obj, name, resolved)

    # If this is a bound method, apply injection to its container instance.
    container = getattr(obj, "__self__", None)
    if container is not None and container is not obj:
        for name, injected in _iter_injected_fields(container):
            try:
                resolved = injected.resolve(scope)
            except InjectionRegistryError as exc:
                if strict:
                    raise ContextBuildError(str(exc)) from exc
                resolved = None
            object.__setattr__(container, name, resolved)


def _apply_config(obj: object, cfg: ConfigScope, strict: bool) -> None:
    for name, cfg_value in _iter_config_fields(obj):
        try:
            resolved = cfg_value.resolve(cfg)
        except KeyError as exc:
            if strict:
                raise ContextBuildError(str(exc)) from exc
            # Non-strict: allow explicit global.* to fall back to root config.
            if cfg_value.path.startswith("global."):
                fallback_scope = ConfigScope(node_cfg=cfg.node_cfg, global_cfg=cfg.root_cfg, root_cfg=cfg.root_cfg)
                try:
                    resolved = cfg_value.resolve(fallback_scope)
                except KeyError:
                    resolved = None
            else:
                resolved = None
        object.__setattr__(obj, name, resolved)

    container = getattr(obj, "__self__", None)
    if container is not None and container is not obj:
        for name, cfg_value in _iter_config_fields(container):
            try:
                resolved = cfg_value.resolve(cfg)
            except KeyError as exc:
                if strict:
                    raise ContextBuildError(str(exc)) from exc
                if cfg_value.path.startswith("global."):
                    fallback_scope = ConfigScope(node_cfg=cfg.node_cfg, global_cfg=cfg.root_cfg, root_cfg=cfg.root_cfg)
                    try:
                        resolved = cfg_value.resolve(fallback_scope)
                    except KeyError:
                        resolved = None
                else:
                    resolved = None
            object.__setattr__(container, name, resolved)
