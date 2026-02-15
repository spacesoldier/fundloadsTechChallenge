from __future__ import annotations

from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.file_io import (
    egress_file_sink,
    ingress_file_source,
    sink_file_sink,
    source_file_source,
)
from stream_kernel.platform.services.state.context import kv_store_memory


def test_file_adapters_are_marked_async_execution_mode() -> None:
    # File ingress/egress adapters are platform IO boundaries and must declare async capability.
    ingress_meta = get_adapter_meta(ingress_file_source)
    source_meta = get_adapter_meta(source_file_source)
    egress_meta = get_adapter_meta(egress_file_sink)
    sink_meta = get_adapter_meta(sink_file_sink)
    assert ingress_meta is not None
    assert source_meta is not None
    assert egress_meta is not None
    assert sink_meta is not None
    assert ingress_meta.execution_mode == "async"
    assert source_meta.execution_mode == "async"
    assert egress_meta.execution_mode == "async"
    assert sink_meta.execution_mode == "async"


def test_memory_kv_adapter_is_marked_sync_execution_mode() -> None:
    # In-memory KV adapter is CPU/memory-local and should stay sync-capable.
    kv_meta = get_adapter_meta(kv_store_memory)
    assert kv_meta is not None
    assert kv_meta.execution_mode == "sync"
