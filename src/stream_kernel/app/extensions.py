from __future__ import annotations

import importlib
from types import ModuleType


class ExtensionDiscoveryError(RuntimeError):
    # Raised when framework extension discovery providers are invalid.
    pass


def framework_discovery_modules() -> list[str]:
    # Collect discovery modules from framework extension providers.
    modules: list[str] = []
    seen: set[str] = set()
    for provider in _load_extension_provider_modules():
        discovery_fn = getattr(provider, "discovery_modules", None)
        if not callable(discovery_fn):
            continue
        declared = discovery_fn()
        if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
            raise ExtensionDiscoveryError(
                f"{provider.__name__}.discovery_modules() must return list[str]"
            )
        for module_name in declared:
            if module_name in seen:
                continue
            seen.add(module_name)
            modules.append(module_name)
    return modules


def _load_extension_provider_modules() -> list[ModuleType]:
    # Built-in framework providers. Each provider exposes discovery_modules().
    provider_names = [
        "stream_kernel.platform",
    ]
    providers: list[ModuleType] = []
    for name in provider_names:
        providers.append(importlib.import_module(name))
    return providers
