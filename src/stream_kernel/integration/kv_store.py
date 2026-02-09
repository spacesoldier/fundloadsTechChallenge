from __future__ import annotations


class KVStore:
    # Generic keyed state port used by runtime services, including context metadata storage.
    def get(self, key: str) -> object | None:
        raise NotImplementedError("KVStore.get must be implemented")

    def set(self, key: str, value: object) -> None:
        raise NotImplementedError("KVStore.set must be implemented")

    def delete(self, key: str) -> None:
        raise NotImplementedError("KVStore.delete must be implemented")


def validate_kv_contract_type(data_type: type[object]) -> None:
    # KV contracts must be KVStore or marker subclasses with the same public API.
    if not isinstance(data_type, type):
        raise TypeError("KV contract must be a class")
    if not issubclass(data_type, KVStore):
        raise TypeError(f"KV contract must inherit from KVStore: {data_type!r}")

    base_api = _public_callable_names(KVStore)
    candidate_api = _public_callable_names(data_type)
    extra_api = sorted(name for name in candidate_api if name not in base_api)
    if extra_api:
        raise TypeError(
            "KV marker contract must not add public methods; "
            f"use @service for richer API ({data_type.__name__}: {extra_api})"
        )


def _public_callable_names(cls: type[object]) -> set[str]:
    names: set[str] = set()
    for name, value in cls.__dict__.items():
        if name.startswith("_"):
            continue
        if callable(value):
            names.add(name)
    return names


class InMemoryKvStore(KVStore):
    # In-memory KV adapter for deterministic local runs and tests.
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self._store.get(key)

    def set(self, key: str, value: object) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
