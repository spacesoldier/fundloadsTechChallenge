from __future__ import annotations

# KV-backed context persistence behavior is specified in
# docs/framework/initial_stage/Execution runtime and routing integration.md.
import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore, validate_kv_contract_type


def test_kv_store_set_get_delete_roundtrip() -> None:
    # Put/get/delete should behave as expected for context metadata.
    store = InMemoryKvStore()
    store.set("trace-1", {"k": "v"})
    assert store.get("trace-1") == {"k": "v"}
    store.delete("trace-1")
    assert store.get("trace-1") is None


def test_kv_store_get_missing_returns_none() -> None:
    # Missing keys should return None (safe default).
    store = InMemoryKvStore()
    assert store.get("missing") is None


def test_kv_store_idempotent_update() -> None:
    # Second put under the same key should overwrite the previous value.
    store = InMemoryKvStore()
    store.set("trace-1", {"a": 1})
    store.set("trace-1", {"a": 2})
    assert store.get("trace-1") == {"a": 2}


class _ContextKVStore(KVStore):
    # Marker contract: same KVStore API, narrowed semantic role.
    pass


class _BadContextKVStore(KVStore):
    # Invalid marker: adds non-platform API and must be rejected.
    def list_keys(self) -> list[str]:
        return []


def test_validate_kv_contract_accepts_base_and_marker_subclass() -> None:
    # Allowed contracts: KVStore itself or marker subclasses with no extra public methods.
    validate_kv_contract_type(KVStore)
    validate_kv_contract_type(_ContextKVStore)


def test_validate_kv_contract_rejects_extended_public_api() -> None:
    # Richer APIs must be modeled as services, not KV contract extensions.
    with pytest.raises(TypeError):
        validate_kv_contract_type(_BadContextKVStore)
