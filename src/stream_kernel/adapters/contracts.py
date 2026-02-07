from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AdapterMeta:
    # Adapter-level routing contracts used by DAG preflight.
    name: str
    kind: str | None
    consumes: tuple[type[object], ...]
    emits: tuple[type[object], ...]
    binds: tuple[tuple[str, type[Any]], ...]


def adapter(
    *,
    name: str | None = None,
    kind: str | None = None,
    consumes: Iterable[type[object]] | None = None,
    emits: Iterable[type[object]] | None = None,
    binds: Iterable[tuple[str, type[Any]]] | None = None,
) -> Callable[[T], T]:
    # Decorator attaches typed consumes/emits contracts to adapter factories.

    def _decorate(target: T) -> T:
        resolved_name = name
        if not isinstance(resolved_name, str) or not resolved_name:
            resolved_name = getattr(target, "__name__", "")
        meta = AdapterMeta(
            name=resolved_name,
            kind=kind,
            consumes=tuple(consumes or ()),
            emits=tuple(emits or ()),
            binds=tuple(binds or ()),
        )
        setattr(target, "__adapter_meta__", meta)
        return target

    return _decorate


def get_adapter_meta(target: object) -> AdapterMeta | None:
    # Read adapter contract metadata if present on callable/class target.
    meta = getattr(target, "__adapter_meta__", None)
    if isinstance(meta, AdapterMeta):
        return meta
    return None
