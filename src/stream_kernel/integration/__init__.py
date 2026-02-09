# Integration package: ports/adapters that bridge external systems.

from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue, WorkQueue

__all__ = [
    "ConsumerRegistry",
    "InMemoryConsumerRegistry",
    "KVStore",
    "InMemoryKvStore",
    "RoutingPort",
    "WorkQueue",
    "InMemoryWorkQueue",
]
