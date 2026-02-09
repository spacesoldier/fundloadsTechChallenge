from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ApplicationContext",
    "ContextBuildError",
    "ServiceMeta",
    "apply_injection",
    "discover_services",
    "service",
    "service_contract_types",
]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid package import cycles across runtime/platform/execution modules.
    if name in {"ApplicationContext", "ContextBuildError", "apply_injection"}:
        module = import_module("stream_kernel.application_context.application_context")
        return getattr(module, name)
    if name in {"ServiceMeta", "discover_services", "service", "service_contract_types"}:
        module = import_module("stream_kernel.application_context.service")
        return getattr(module, name)
    raise AttributeError(name)

