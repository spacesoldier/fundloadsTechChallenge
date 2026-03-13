from __future__ import annotations

from typing import Any

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
)
from stream_kernel.platform.services.runtime.control_plane_startup_bindings import (
    ControlPlaneStartupConsumerBindingsService,
)


def merge_consumer_maps(
    *maps: dict[type[Any], list[str]],
) -> dict[type[Any], list[str]]:
    merged: dict[type[Any], list[str]] = {}
    for mapping in maps:
        for token, node_names in mapping.items():
            existing = merged.setdefault(token, [])
            for node_name in node_names:
                if node_name not in existing:
                    existing.append(node_name)
    return merged


def seed_startup_consumer_bindings(
    *,
    scenario_scope: ScenarioScope,
    consumers: dict[type[Any], list[str]],
) -> None:
    if not consumers:
        return
    bindings: list[ControlPlaneConsumerBindingRecord] = []
    for token, node_names in consumers.items():
        if not isinstance(token, type):
            continue
        normalized = tuple(
            name
            for name in node_names
            if isinstance(name, str) and name
        )
        if not normalized:
            continue
        bindings.append(
            ControlPlaneConsumerBindingRecord(
                token=token,
                node_names=normalized,
            )
        )
    if not bindings:
        return
    resolve = getattr(scenario_scope, "resolve", None)
    if not callable(resolve):
        return
    try:
        service = resolve("service", ControlPlaneStartupConsumerBindingsService)
    except Exception:
        return
    seed = getattr(service, "seed_bindings", None)
    if callable(seed):
        seed(tuple(bindings))


__all__ = [
    "merge_consumer_maps",
    "seed_startup_consumer_bindings",
]

