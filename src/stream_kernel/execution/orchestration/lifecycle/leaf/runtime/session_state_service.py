from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service


@runtime_checkable
class LeafRuntimeSessionStateService(Protocol):
    def bind_session(self, session: object) -> None:
        raise NotImplementedError

    def clear_session(self) -> None:
        raise NotImplementedError

    def current_session(self) -> object | None:
        raise NotImplementedError


@service(name="leaf_runtime_session_state_service")
@dataclass(slots=True)
class DefaultLeafRuntimeSessionStateService(LeafRuntimeSessionStateService):
    _session: object | None = None

    def bind_session(self, session: object) -> None:
        self._session = session

    def clear_session(self) -> None:
        self._session = None

    def current_session(self) -> object | None:
        return self._session


__all__ = [
    "LeafRuntimeSessionStateService",
    "DefaultLeafRuntimeSessionStateService",
]
