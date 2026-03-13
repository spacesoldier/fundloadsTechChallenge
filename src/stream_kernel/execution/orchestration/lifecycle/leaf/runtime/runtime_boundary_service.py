from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service


@runtime_checkable
class LeafRuntimeBoundaryBatchService(Protocol):
    def execute_boundary_batch(
        self,
        *,
        child: object,
        inputs: list[object],
        finalize_runtime: bool = False,
        stream_callback: Callable[[list[object]], bool] | None = None,
        stream_batch_max_items: int = 1,
    ) -> list[object]:
        raise NotImplementedError


@service(name="leaf_runtime_boundary_batch_service")
@dataclass(slots=True)
class DefaultLeafRuntimeBoundaryBatchService(LeafRuntimeBoundaryBatchService):
    def execute_boundary_batch(
        self,
        *,
        child: object,
        inputs: list[object],
        finalize_runtime: bool = False,
        stream_callback: Callable[[list[object]], bool] | None = None,
        stream_batch_max_items: int = 1,
    ) -> list[object]:
        return list(
            execute_child_boundary_loop_with_runtime(
                child=child,
                inputs=list(inputs),
                finalize=bool(finalize_runtime),
                stream_callback=stream_callback,
                stream_batch_max_items=stream_batch_max_items,
            )
        )


def execute_child_boundary_loop_with_runtime(
    *,
    child: object,
    inputs: list[object],
    finalize: bool = False,
    stream_callback: Callable[[list[object]], bool] | None = None,
    stream_batch_max_items: int = 1,
):
    from .boundary_runtime import execute_child_boundary_loop_with_runtime as impl

    return impl(
        child=child,
        inputs=inputs,
        finalize=finalize,
        stream_callback=stream_callback,
        stream_batch_max_items=stream_batch_max_items,
    )


__all__ = [
    "LeafRuntimeBoundaryBatchService",
    "DefaultLeafRuntimeBoundaryBatchService",
]
