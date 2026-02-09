from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class NodeMeta:
    # Metadata attached to a node/step for discovery and wiring.
    name: str
    stage: str = ""
    consumes: list[type[object]] = field(default_factory=list)
    emits: list[type[object]] = field(default_factory=list)
    service: bool = False

    def __post_init__(self) -> None:
        # Guardrails to keep metadata deterministic and explicit.
        if not self.name:
            raise ValueError("NodeMeta.name must be a non-empty string")
        if len(self.consumes) != len(set(self.consumes)):
            raise ValueError("NodeMeta.consumes must not contain duplicates")
        if len(self.emits) != len(set(self.emits)):
            raise ValueError("NodeMeta.emits must not contain duplicates")


@dataclass(frozen=True, slots=True)
class NodeDef:
    # A concrete node definition used by the runtime and application context.
    meta: NodeMeta
    target: object
    container_cls: type | None = None
    container_attr: str | None = None


# Re-export decorator for backward compatibility; implementation lives in node_annotation.py.
from stream_kernel.kernel.node_annotation import node
