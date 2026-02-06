from __future__ import annotations


class ContextStore:
    # Port for storing per-message context metadata (Execution runtime and routing integration §3.3).
    def get(self, key: str) -> object | None:
        raise NotImplementedError("ContextStore.get must be implemented")

    def put(self, key: str, ctx: object) -> None:
        raise NotImplementedError("ContextStore.put must be implemented")

    def delete(self, key: str) -> None:
        raise NotImplementedError("ContextStore.delete must be implemented")


class InMemoryContextStore(ContextStore):
    # In-memory context store for tests and local runs (Execution runtime and routing integration §8.2).
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self._store.get(key)

    def put(self, key: str, ctx: object) -> None:
        self._store[key] = ctx

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
