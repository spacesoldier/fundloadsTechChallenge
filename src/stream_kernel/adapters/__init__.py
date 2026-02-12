from .contracts import AdapterMeta, adapter, get_adapter_meta
from .discovery import AdapterDiscoveryError, discover_adapters
from .file_io import (
    ByteRecord,
    SinkLine,
    StreamLine,
    TextRecord,
    egress_file_sink,
    ingress_file_source,
    sink_file_sink,
    source_file_source,
)
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
    "ByteRecord",
    "TextRecord",
    "StreamLine",
    "SinkLine",
    "ingress_file_source",
    "egress_file_sink",
    "source_file_source",
    "sink_file_sink",
    "JsonlTraceSink",
    "StdoutTraceSink",
    "AdapterWiringError",
    "build_injection_registry",
]
