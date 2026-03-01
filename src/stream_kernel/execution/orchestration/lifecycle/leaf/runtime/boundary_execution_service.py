from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.service import service

if TYPE_CHECKING:
    from .worker_runtime import LeafWorkerRuntimeSession


@runtime_checkable
class LeafBoundaryExecutionService(Protocol):
    def execute(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        inputs: list[object],
        finalize_runtime: bool = False,
    ) -> list[object]:
        raise NotImplementedError


@service(name="leaf_boundary_execution_service")
@dataclass(slots=True)
class DefaultLeafBoundaryExecutionService(LeafBoundaryExecutionService):
    def execute(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        inputs: list[object],
        finalize_runtime: bool = False,
    ) -> list[object]:
        return list(
            execute_leaf_boundary_batch(
                session=session,
                inputs=list(inputs),
                finalize_runtime=bool(finalize_runtime),
            )
        )


def execute_leaf_boundary_batch(
    *,
    session: "LeafWorkerRuntimeSession",
    inputs: list[object],
    finalize_runtime: bool = False,
):
    from .worker_runtime import execute_leaf_boundary_batch as impl

    return impl(session=session, inputs=inputs, finalize_runtime=finalize_runtime)


__all__ = [
    "LeafBoundaryExecutionService",
    "DefaultLeafBoundaryExecutionService",
]
