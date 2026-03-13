from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ControlPlaneRootRuntimeBootstrapService(Protocol):
    def prepare_root_runtime(
        self,
        *,
        runtime: dict[str, object],
        config: dict[str, object],
        adapters: dict[str, object],
        run_id: str,
        scenario_id: str,
        discovery_modules: list[str],
    ) -> None:
        raise NotImplementedError


__all__ = [
    "ControlPlaneRootRuntimeBootstrapService",
]
