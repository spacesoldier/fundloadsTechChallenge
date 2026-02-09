from __future__ import annotations

from types import ModuleType
from typing import Callable

from stream_kernel.adapters.contracts import AdapterMeta, get_adapter_meta


class AdapterDiscoveryError(RuntimeError):
    # Raised when adapter discovery finds duplicate names or invalid metadata.
    pass


def discover_adapters(modules: list[ModuleType]) -> dict[str, Callable[[dict[str, object]], object]]:
    # Discover adapter factories declared via @adapter(name=...).
    discovered: dict[str, Callable[[dict[str, object]], object]] = {}
    seen_targets: dict[str, object] = {}
    for module in modules:
        for value in module.__dict__.values():
            meta = get_adapter_meta(value)
            if not isinstance(meta, AdapterMeta):
                continue
            if not isinstance(meta.name, str) or not meta.name:
                # Adapters without stable name are intentionally ignored by discovery.
                continue
            if not callable(value):
                raise AdapterDiscoveryError(f"Adapter '{meta.name}' target is not callable")
            if meta.name in discovered:
                if seen_targets.get(meta.name) is value:
                    # Same callable re-exported through another module is not a conflict.
                    continue
                raise AdapterDiscoveryError(f"Duplicate adapter name discovered: {meta.name}")
            discovered[meta.name] = value
            seen_targets[meta.name] = value
    return discovered
