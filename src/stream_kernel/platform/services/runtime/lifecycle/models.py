from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass

from stream_kernel.integration.kv_store import KVStore


class ExecutionWorkerRegistry(KVStore):
    # KV marker contract for worker lifecycle registry.
    # Values are ExecutionWorkerHandle instances keyed by target_id.
    pass


@dataclass(frozen=True, slots=True)
class ExecutionWorkerHandle:
    target_id: str
    process: mp.Process
    stop_event: object | None
    control_parent: object | None


__all__ = [
    "ExecutionWorkerRegistry",
    "ExecutionWorkerHandle",
]

