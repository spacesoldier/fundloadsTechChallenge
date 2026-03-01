from __future__ import annotations

import copy


def is_child_bootstrap_bundle(bundle: object | None) -> bool:
    try:
        from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import ChildBootstrapBundle
    except Exception:
        return False
    return isinstance(bundle, ChildBootstrapBundle)


def resolve_child_process_role(*, runtime: dict[str, object], group_name: str) -> str:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return "worker"
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict) or not service_process:
        service_process = observability.get("service_worker", {})
        if not isinstance(service_process, dict):
            return "worker"
    enabled = service_process.get("enabled", False)
    if not isinstance(enabled, bool) or not enabled:
        return "worker"
    owner_group = service_process.get("group_name", "system.observability")
    if isinstance(owner_group, str) and owner_group and owner_group == group_name:
        return "observability_worker"
    return "worker"


def project_child_bundle_for_group(
    bundle: object | None,
    group_name: str,
    *,
    runner_profile: str | None = None,
) -> object | None:
    if not is_child_bootstrap_bundle(bundle):
        return bundle
    from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import ChildBootstrapBundle

    runtime_raw = getattr(bundle, "runtime")
    runtime_copy = copy.deepcopy(runtime_raw) if isinstance(runtime_raw, dict) else {}
    runtime_copy["__process_role"] = resolve_child_process_role(
        runtime=runtime_copy,
        group_name=group_name,
    )
    if isinstance(runner_profile, str) and runner_profile:
        runtime_copy["__runner_profile_requested"] = runner_profile

    return ChildBootstrapBundle(
        scenario_id=getattr(bundle, "scenario_id"),
        process_group=group_name,
        discovery_modules=list(getattr(bundle, "discovery_modules")),
        runtime=runtime_copy,
        key_bundle=getattr(bundle, "key_bundle"),
        run_id=getattr(bundle, "run_id", "run"),
        adapters=dict(getattr(bundle, "adapters", {}) or {}),
        config=dict(getattr(bundle, "config", {}) or {}),
    )


__all__ = [
    "is_child_bootstrap_bundle",
    "project_child_bundle_for_group",
    "resolve_child_process_role",
]
