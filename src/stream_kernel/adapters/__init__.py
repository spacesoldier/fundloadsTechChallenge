from .contracts import AdapterMeta, adapter, get_adapter_meta
from .discovery import AdapterDiscoveryError, discover_adapters
from .registry import AdapterRegistry, AdapterRegistryError
from .trace_sinks import JsonlTraceSink, StdoutTraceSink
from .wiring import AdapterWiringError, build_injection_registry
from stream_kernel.observability.adapters import log_stdout, telemetry_stdout, trace_jsonl, trace_stdout

__all__ = [
    "adapter",
    "AdapterMeta",
    "get_adapter_meta",
    "discover_adapters",
    "AdapterDiscoveryError",
    "trace_stdout",
    "trace_jsonl",
    "log_stdout",
    "telemetry_stdout",
    "AdapterRegistry",
    "AdapterRegistryError",
    "JsonlTraceSink",
    "StdoutTraceSink",
    "AdapterWiringError",
    "build_injection_registry",
]
