from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_message_dispatch import (
    RuntimeDebugMessageDispatchService,
)
from stream_kernel.routing.envelope import Envelope


@node(name="system.debug.message_dispatch", consumes=[DebugMessage], emits=[])
@dataclass
class RuntimeDebugMessageDispatchNode:
    service: RuntimeDebugMessageDispatchService
    qualifier: str | None = None
    _marker: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._marker = inject.service(RuntimeDebugMessageDispatchService, qualifier=self.qualifier)

    async def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, DebugMessage):
            return []
        results = self.service.dispatch(message=payload)
        for item in results:
            if inspect.isawaitable(item):
                try:
                    await item
                except Exception:
                    pass
        return []


@dataclass(frozen=True, slots=True)
class DebugSystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


def build_debug_system_plan(
    *,
    runtime: dict[str, object],
    scenario_scope: object | None = None,
) -> DebugSystemPlan:
    if not _is_runtime_debug_exporter_enabled(runtime):
        return DebugSystemPlan()
    if scenario_scope is None:
        raise RuntimeError("runtime debug dispatch requires scenario_scope for DI injection")
    node_name = "system.debug.message_dispatch"
    dispatch_node = RuntimeDebugMessageDispatchNode(
        service=inject.service(RuntimeDebugMessageDispatchService),
        qualifier=None,
    )
    apply_injection(dispatch_node, scenario_scope, True)
    # Keep async planning marker after DI resolves runtime fields.
    dispatch_node._marker = inject.service(RuntimeDebugMessageDispatchService, qualifier=None)
    return DebugSystemPlan(
        system_steps=[StepSpec(name=node_name, step=dispatch_node)],
        system_consumers={DebugMessage: [node_name]},
        system_node_names={node_name},
    )


def _is_runtime_debug_exporter_enabled(runtime: dict[str, object]) -> bool:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    logging_cfg = observability.get("logging", {})
    if not isinstance(logging_cfg, dict):
        return False
    exporters = logging_cfg.get("exporters")
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("kind") == "redis_debug"
        and item.get("enabled", True) is not False
        for item in exporters
    )


def _coerce_outputs(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return [value]


__all__ = [
    "DebugSystemPlan",
    "RuntimeDebugMessageDispatchNode",
    "build_debug_system_plan",
]
