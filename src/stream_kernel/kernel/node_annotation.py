from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from stream_kernel.kernel.node import NodeMeta

T = TypeVar("T")


def node(
    *,
    name: str,
    stage: str = "",
    consumes: Iterable[type[object]] | None = None,
    emits: Iterable[type[object]] | None = None,
    service: bool = False,
) -> Callable[[T], T]:
    # Decorator attaches NodeMeta to a callable/class for discovery.
    consume_list = list(consumes or [])
    emit_list = list(emits or [])
    meta = NodeMeta(
        name=name,
        stage=stage,
        consumes=consume_list,
        emits=emit_list,
        service=service,
    )

    def _decorate(target: T) -> T:
        setattr(target, "__node_meta__", meta)
        return target

    return _decorate
