# Integration package: ports/adapters that bridge external systems.

from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.integration.context_store import ContextStore, InMemoryContextStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue, WorkQueue

__all__ = [
    "ConsumerRegistry",
    "InMemoryConsumerRegistry",
    "ContextStore",
    "InMemoryContextStore",
    "RoutingPort",
    "WorkQueue",
    "InMemoryWorkQueue",
]
