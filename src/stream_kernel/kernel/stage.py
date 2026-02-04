from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from stream_kernel.kernel.node import NodeDef


@dataclass(frozen=True, slots=True)
class StageDef:
    # A stage groups nodes for diagnostics or deployment planning.
    name: str
    nodes: list[NodeDef]


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StageMeta:
    # Metadata attached to a stage container.
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("StageMeta.name must be a non-empty string")


def stage(*, name: str) -> Callable[[T], T]:
    # Decorator attaches StageMeta to a container class for discovery.
    meta = StageMeta(name=name)

    def _decorate(target: T) -> T:
        setattr(target, "__stage_meta__", meta)
        return target

    return _decorate
