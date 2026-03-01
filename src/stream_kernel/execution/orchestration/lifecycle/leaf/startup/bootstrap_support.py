from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry

from .bootstrap_models import ChildBootstrapBundle, ChildRuntimeBootstrapError


def validate_child_bootstrap_bundle(bundle: object) -> ChildBootstrapBundle:
    if not isinstance(bundle, ChildBootstrapBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle must be ChildBootstrapBundle")
    if not isinstance(bundle.scenario_id, str) or not bundle.scenario_id:
        raise ChildRuntimeBootstrapError("child bootstrap bundle.scenario_id must be a non-empty string")
    if not isinstance(bundle.run_id, str) or not bundle.run_id:
        raise ChildRuntimeBootstrapError("child bootstrap bundle.run_id must be a non-empty string")
    if bundle.process_group is not None and (not isinstance(bundle.process_group, str) or not bundle.process_group):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.process_group must be null or non-empty string")
    if not isinstance(bundle.discovery_modules, list) or not all(
        isinstance(item, str) and item for item in bundle.discovery_modules
    ):
        raise ChildRuntimeBootstrapError(
            "child bootstrap bundle.discovery_modules must be list[str] with non-empty entries"
        )
    if not isinstance(bundle.runtime, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.runtime must be a mapping")
    if bundle.config is not None and not isinstance(bundle.config, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.config must be a mapping")
    if bundle.adapters is not None and not isinstance(bundle.adapters, dict):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.adapters must be a mapping")
    from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import BootstrapKeyBundle

    if not isinstance(bundle.key_bundle, BootstrapKeyBundle):
        raise ChildRuntimeBootstrapError("child bootstrap bundle.key_bundle must be BootstrapKeyBundle")
    return bundle


def register_adapter_bindings(
    *,
    injection_registry: InjectionRegistry,
    instances: dict[str, object],
    bindings: dict[str, object],
    adapter_async_roles: set[str] | None = None,
) -> None:
    async_roles = set(adapter_async_roles or ())
    for role, binding in bindings.items():
        if role not in instances:
            raise ChildRuntimeBootstrapError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        is_async = role in async_roles
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection_registry.register_factory(
                    port_type,
                    data_type,
                    lambda _a=adapter: _a,
                    is_async=is_async,
                )
            continue
        port_type, data_type = binding
        injection_registry.register_factory(
            port_type,
            data_type,
            lambda _a=adapter: _a,
            is_async=is_async,
        )


def classify_async_bindings(registry: InjectionRegistry) -> tuple[list[str], list[str]]:
    service_contracts: set[str] = set()
    adapter_bindings: set[str] = set()
    for port_type, data_type, qualifier in registry.list_async_bindings():
        contract_name = getattr(data_type, "__name__", str(data_type))
        if port_type == "service":
            service_contracts.add(contract_name)
            continue
        suffix = f"#{qualifier}" if isinstance(qualifier, str) and qualifier else ""
        adapter_bindings.add(f"{port_type}<{contract_name}>{suffix}")
    return (sorted(service_contracts), sorted(adapter_bindings))


def collect_observability_exporter_summary(
    *,
    runtime: dict[str, object],
    adapter_instances: dict[str, object],
) -> list[str]:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return []
    summary: list[str] = []
    tracing = observability.get("tracing", {})
    if isinstance(tracing, dict):
        exporters = tracing.get("exporters", [])
        if isinstance(exporters, list):
            alias_map = {
                "jsonl": "trace_jsonl",
                "stdout": "trace_stdout",
                "otel_otlp": "trace_otel_otlp",
                "otel_otlp_logical": "trace_otel_otlp",
                "otel_otlp_topology": "trace_otel_otlp",
                "opentracing_bridge": "trace_opentracing_bridge",
            }
            for index, exporter in enumerate(exporters):
                if not isinstance(exporter, dict):
                    continue
                if exporter.get("enabled") is False:
                    continue
                kind = exporter.get("kind")
                if not isinstance(kind, str) or not kind:
                    continue
                settings = exporter.get("settings", {})
                if not isinstance(settings, dict):
                    settings = {}
                transport = settings.get("transport")
                if not isinstance(transport, dict):
                    transport = {}
                backend = transport.get("backend", settings.get("backend", "-"))
                httpx = transport.get("httpx")
                if not isinstance(httpx, dict):
                    httpx = settings.get("httpx", {})
                if not isinstance(httpx, dict):
                    httpx = {}
                mode = httpx.get("mode", "-")
                alias = alias_map.get(kind)
                candidate = adapter_instances.get(f"{alias}#{index}") if isinstance(alias, str) else None
                if candidate is None:
                    candidate = adapter_instances.get(alias) if isinstance(alias, str) else None
                supports_emit_async = callable(getattr(candidate, "emit_async", None))
                summary.append(
                    "tracing[{index}] kind={kind} backend={backend} httpx_mode={mode} emit_async={emit_async}".format(
                        index=index,
                        kind=kind,
                        backend=backend if isinstance(backend, str) and backend else "-",
                        mode=mode if isinstance(mode, str) and mode else "-",
                        emit_async="yes" if supports_emit_async else "no",
                    )
                )
    return summary


__all__ = [
    "validate_child_bootstrap_bundle",
    "register_adapter_bindings",
    "classify_async_bindings",
    "collect_observability_exporter_summary",
]
