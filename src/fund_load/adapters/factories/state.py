from __future__ import annotations

from typing import Any

from fund_load.adapters.state.window_store import InMemoryWindowStore
from fund_load.ports.window_store import WindowReadPort, WindowWritePort
from stream_kernel.adapters.contracts import adapter


@adapter(
    name="window_store",
    kind="memory.window_store",
    consumes=[],
    emits=[],
    binds=[("kv", WindowReadPort), ("kv", WindowWritePort)],
)
def window_store_memory(settings: dict[str, Any]) -> InMemoryWindowStore:
    # Factory for in-memory window store.
    return InMemoryWindowStore()
