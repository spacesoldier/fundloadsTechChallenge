from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.service import service
from stream_kernel.platform.services.observability import ObservabilityService
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
    LeafLifecycleDebugLoggingService,
    flush_leaf_debug_logging,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


@runtime_checkable
class LeafSessionFinalizationService(Protocol):
    def finalize(self, *, session: "LeafWorkerRuntimeSession") -> None:
        raise NotImplementedError


@service(name="leaf_session_finalization_service")
@dataclass(slots=True)
class DefaultLeafSessionFinalizationService(LeafSessionFinalizationService):
    def finalize(self, *, session: "LeafWorkerRuntimeSession") -> None:
        child = getattr(session, "child", None)
        scope = getattr(child, "scenario_scope", None)
        debug_service = None
        if scope is not None:
            try:
                debug_service = scope.resolve("service", LeafLifecycleDebugLoggingService)
            except Exception:
                debug_service = None
        try:
            flush_leaf_debug_logging(service=debug_service)
        except Exception:
            pass
        if scope is None:
            return
        try:
            observability = scope.resolve("service", ObservabilityService)
        except Exception:
            observability = None
        callback = getattr(observability, "on_run_end", None)
        if callable(callback):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    try:
                        asyncio.run(result)
                    except RuntimeError:
                        pass
            except Exception:
                pass
        closer = getattr(scope, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


__all__ = [
    "LeafSessionFinalizationService",
    "DefaultLeafSessionFinalizationService",
]
