from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
_SUPPORTED_ADAPTER_EXECUTION_MODES = {"sync", "async", "any"}


@dataclass(frozen=True, slots=True)
class AdapterMeta:
    # Adapter-level routing contracts used by DAG preflight.
    name: str
    kind: str | None
    consumes: tuple[type[object], ...]
    emits: tuple[type[object], ...]
    binds: tuple[tuple[str, type[Any]], ...]
    execution_mode: str = "sync"

    def __post_init__(self) -> None:
        if self.execution_mode not in _SUPPORTED_ADAPTER_EXECUTION_MODES:
            raise ValueError(
                "AdapterMeta.execution_mode must be one of: "
                f"{sorted(_SUPPORTED_ADAPTER_EXECUTION_MODES)}"
            )


def adapter(
    *,
    name: str | None = None,
    kind: str | None = None,
    consumes: Iterable[type[object]] | None = None,
    emits: Iterable[type[object]] | None = None,
    binds: Iterable[tuple[str, type[Any]]] | None = None,
    execution_mode: str = "sync",
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
            execution_mode=execution_mode,
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


@runtime_checkable
class TraceSinkPort(Protocol):
    # Platform port for trace record emission.
    # All trace sink adapters must implement these three methods.
    # Async capability is declared via @adapter(execution_mode=...) on the factory,
    # not via this interface — the interface itself is execution-mode agnostic.
    def emit(self, record: object) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class BusinessDispatchPort(Protocol):
    # Transport-agnostic business dispatch port used by supervisor boundary execution path.
    def send(
        self,
        *,
        handle: object,
        command: dict[str, object],
        timeout_seconds: float,
        raise_on_timeout: bool = True,
    ) -> dict[str, object]: ...

    def send_no_wait(
        self,
        *,
        handle: object,
        command: dict[str, object],
    ) -> None: ...


@runtime_checkable
class ControlPlaneDispatchPort(Protocol):
    # Transport-agnostic control-plane port for system/service commands.
    def send_no_wait(
        self,
        *,
        handle: object,
        command: dict[str, object],
    ) -> None: ...
