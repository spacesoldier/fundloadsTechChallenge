from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

import inspect

from stream_kernel.kernel.node import NodeMeta, NodeDef
from stream_kernel.kernel.stage import StageMeta


class NodeDiscoveryError(RuntimeError):
    # Raised when discovery finds duplicates or invalid metadata.
    pass


def _collect_nodes(
    values: list[object],
    *,
    default_stage: str | None = None,
    service_default: bool = False,
) -> list[NodeDef]:
    found: list[NodeDef] = []
    for value in values:
        meta = getattr(value, "__node_meta__", None)
        if not isinstance(meta, NodeMeta):
            continue
        stage = meta.stage or (default_stage or "")
        found.append(
            NodeDef(
                meta=NodeMeta(
                    name=meta.name,
                    stage=stage,
                    consumes=meta.consumes,
                    emits=meta.emits,
                    service=bool(meta.service or service_default),
                ),
                target=value,
            )
        )
    return found


def discover_nodes(modules: list[ModuleType]) -> list[NodeDef]:
    # Discover objects with __node_meta__ in provided modules, including stage containers.
    found: list[NodeDef] = []
    seen: set[str] = set()

    for module in modules:
        is_platform_observer = module.__name__.startswith("stream_kernel.observability.observers")
        values = list(module.__dict__.values())
        # Top-level nodes.
        found.extend(_collect_nodes(values, service_default=is_platform_observer))

        # Stage containers: scan their attributes for nodes.
        for value in values:
            stage_meta = getattr(value, "__stage_meta__", None)
            if not isinstance(stage_meta, StageMeta):
                continue
            container_dict = getattr(value, "__dict__", {})
            for attr_name, attr_value in container_dict.items():
                meta = getattr(attr_value, "__node_meta__", None)
                if not isinstance(meta, NodeMeta):
                    continue
                stage = meta.stage or stage_meta.name
                if inspect.isfunction(attr_value):
                    found.append(
                        NodeDef(
                            meta=NodeMeta(
                                name=meta.name,
                                stage=stage,
                                consumes=meta.consumes,
                                emits=meta.emits,
                                service=bool(meta.service or is_platform_observer),
                            ),
                            target=attr_value,
                            container_cls=value,
                            container_attr=attr_name,
                        )
                    )
                else:
                    found.append(
                        NodeDef(
                            meta=NodeMeta(
                                name=meta.name,
                                stage=stage,
                                consumes=meta.consumes,
                                emits=meta.emits,
                                service=bool(meta.service or is_platform_observer),
                            ),
                            target=attr_value,
                        )
                    )

    # Enforce unique node names across all discoveries.
    unique: list[NodeDef] = []
    seen_targets: dict[str, object] = {}
    for node in found:
        if node.meta.name in seen:
            if seen_targets.get(node.meta.name) is node.target:
                # Same callable/class can be re-exported across modules; keep a single declaration.
                continue
            raise NodeDiscoveryError(f"Duplicate node name discovered: {node.meta.name}")
        seen.add(node.meta.name)
        seen_targets[node.meta.name] = node.target
        unique.append(node)

    return unique
