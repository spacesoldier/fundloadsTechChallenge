from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import BootstrapKeyBundle

from .bootstrap_models import ChildBootstrapBundle


def build_child_bootstrap_bundle(
    *,
    scenario_id: str,
    run_id: str = "run",
    process_group: str | None,
    discovery_modules: list[str],
    runtime: dict[str, object],
    config: dict[str, object] | None = None,
    adapters: dict[str, object] | None = None,
    key_bundle: BootstrapKeyBundle,
) -> ChildBootstrapBundle:
    return ChildBootstrapBundle(
        scenario_id=scenario_id,
        process_group=process_group,
        discovery_modules=list(discovery_modules),
        config=dict(config) if isinstance(config, dict) else None,
        runtime=dict(runtime),
        key_bundle=key_bundle,
        run_id=run_id,
        adapters=dict(adapters or {}),
    )


__all__ = ["build_child_bootstrap_bundle"]
