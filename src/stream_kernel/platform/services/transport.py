from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.transport.secure_tcp_transport import SecureTcpTransport
from stream_kernel.integration.work_queue import (
    InMemoryQueue,
    InMemoryTopic,
    QueuePort,
    TcpLocalQueue,
    TcpLocalTopic,
    TopicPort,
)


class RuntimeTransportService:
    # Runtime transport profile contract used by execution builder.
    profile: str

    def build_queue(self) -> QueuePort:
        raise NotImplementedError("RuntimeTransportService.build_queue must be implemented")

    def build_topic(self) -> TopicPort:
        raise NotImplementedError("RuntimeTransportService.build_topic must be implemented")


@dataclass(slots=True)
class MemoryRuntimeTransportService(RuntimeTransportService):
    # In-process runtime transport profile.
    profile: str = "memory"

    def build_queue(self) -> QueuePort:
        return InMemoryQueue()

    def build_topic(self) -> TopicPort:
        return InMemoryTopic()


@dataclass(slots=True)
class TcpLocalRuntimeTransportService(RuntimeTransportService):
    # Localhost secure transport profile.
    transport: SecureTcpTransport
    profile: str = "tcp_local"

    def build_queue(self) -> QueuePort:
        return TcpLocalQueue(transport=self.transport)

    def build_topic(self) -> TopicPort:
        return TcpLocalTopic(transport=self.transport)
