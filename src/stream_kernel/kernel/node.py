from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NodeMeta:
    # Metadata attached to a node/step for discovery and wiring.
    name: str
    stage: str = ""
    requires: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Guardrails to keep metadata deterministic and explicit.
        if not self.name:
            raise ValueError("NodeMeta.name must be a non-empty string")
        if len(self.requires) != len(set(self.requires)):
            raise ValueError("NodeMeta.requires must not contain duplicates")
        if len(self.provides) != len(set(self.provides)):
            raise ValueError("NodeMeta.provides must not contain duplicates")


@dataclass(frozen=True, slots=True)
class NodeDef:
    # A concrete node definition used by the runtime and application context.
    meta: NodeMeta
    target: object
    container_cls: type | None = None
    container_attr: str | None = None




def node(
    *,
    name: str,
    stage: str = "",
    requires: Iterable[str] | None = None,
    provides: Iterable[str] | None = None,
) -> Callable[[T], T]:
    # Decorator attaches NodeMeta to a callable/class for discovery.
    req_list = list(requires or [])
    prov_list = list(provides or [])
    meta = NodeMeta(name=name, stage=stage, requires=req_list, provides=prov_list)

    def _decorate(target: T) -> T:
        setattr(target, "__node_meta__", meta)
        return target

    return _decorate
