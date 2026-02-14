from __future__ import annotations

from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.platform.services.bootstrap import (
    BootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
    LocalBootstrapSupervisor,
)
from stream_kernel.platform.services.consumer_registry import DiscoveryConsumerRegistry
from stream_kernel.platform.services.context import ContextService, InMemoryKvContextService, kv_store_memory
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService,
    ReplyAwareObservabilityService,
    legacy_reply_aware_observability,
)
from stream_kernel.platform.services.reply_waiter import (
    InMemoryReplyWaiterService,
    PendingReplyWaiterService,
    ReplyWaiterService,
    TerminalEvent,
)
from stream_kernel.platform.services.reply_coordinator import (
    InMemoryReplyCoordinatorService,
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.lifecycle import (
    LocalRuntimeLifecycleManager,
    RuntimeLifecycleManager,
)
from stream_kernel.platform.services.transport import (
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)

__all__ = [
    "ConsumerRegistry",
    "BootstrapSupervisor",
    "ContextService",
    "DiscoveryConsumerRegistry",
    "InMemoryKvContextService",
    "MultiprocessBootstrapSupervisor",
    "LocalBootstrapSupervisor",
    "LocalRuntimeLifecycleManager",
    "NoOpObservabilityService",
    "ObservabilityService",
    "ReplyAwareObservabilityService",
    "InMemoryReplyWaiterService",
    "InMemoryReplyCoordinatorService",
    "PendingReplyWaiterService",
    "ReplyCoordinatorService",
    "ReplyWaiterService",
    "RuntimeLifecycleManager",
    "RuntimeTransportService",
    "TerminalEvent",
    "legacy_reply_aware_observability",
    "legacy_reply_coordinator",
    "MemoryRuntimeTransportService",
    "TcpLocalRuntimeTransportService",
    "kv_store_memory",
]
