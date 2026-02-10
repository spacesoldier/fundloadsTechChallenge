from .contracts import AdapterMeta, adapter, get_adapter_meta
from .discovery import AdapterDiscoveryError, discover_adapters
from .registry import AdapterRegistry, AdapterRegistryError
from .trace_sinks import JsonlTraceSink, StdoutTraceSink
from .wiring import AdapterWiringError, build_injection_registry

__all__ = [
    "adapter",
    "AdapterMeta",
    "get_adapter_meta",
    "discover_adapters",
    "AdapterDiscoveryError",
    "AdapterRegistry",
    "AdapterRegistryError",
    "JsonlTraceSink",
    "StdoutTraceSink",
    "AdapterWiringError",
    "build_injection_registry",
]
