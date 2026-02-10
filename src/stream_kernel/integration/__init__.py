# Integration package: ports/adapters that bridge external systems.

from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.integration.work_queue import InMemoryQueue, InMemoryTopic, QueuePort, TopicPort

__all__ = [
    "ConsumerRegistry",
    "InMemoryConsumerRegistry",
    "KVStore",
    "InMemoryKvStore",
    "QueuePort",
    "InMemoryQueue",
    "TopicPort",
    "InMemoryTopic",
]
