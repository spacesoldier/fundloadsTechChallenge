from __future__ import annotations

# ContextStore behavior is specified in docs/framework/initial_stage/Execution runtime and routing integration.md.
from stream_kernel.integration.context_store import InMemoryContextStore


def test_context_store_put_get_delete_roundtrip() -> None:
    # Put/get/delete should behave as expected for context metadata.
    store = InMemoryContextStore()
    store.put("trace-1", {"k": "v"})
    assert store.get("trace-1") == {"k": "v"}
    store.delete("trace-1")
    assert store.get("trace-1") is None


def test_context_store_get_missing_returns_none() -> None:
    # Missing keys should return None (safe default).
    store = InMemoryContextStore()
    assert store.get("missing") is None


def test_context_store_idempotent_update() -> None:
    # Second put under the same key should overwrite the previous value.
    store = InMemoryContextStore()
    store.put("trace-1", {"a": 1})
    store.put("trace-1", {"a": 2})
    assert store.get("trace-1") == {"a": 2}
