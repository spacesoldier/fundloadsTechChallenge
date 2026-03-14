from __future__ import annotations

from stream_kernel.integration.consumer_registry import ConsumerRegistry, ConsumerRegistryStore
from stream_kernel.platform.services.api.policy import (
    ApiPolicyService,
    InMemoryApiPolicyService,
    InMemoryRateLimiterService,
    RateLimiterService,
)
from stream_kernel.platform.services.api.outbound import (
    InMemoryOutboundApiService,
    OutboundApiService,
    OutboundCircuitOpenError,
    OutboundRateLimitedError,
)
from stream_kernel.platform.services.state.consumer_registry import DiscoveryConsumerRegistry
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService, kv_store_memory
from stream_kernel.platform.services.observability import (
    coerce_pipeline_observability,
    DefaultWorkerQueueTelemetryService,
    InMemoryObservabilityMetricsService,
    NoOpObservabilityService,
    ObservabilityMetricsService,
    ObservabilityPipelineService,
    ObservabilityService,
    ReplyAwareObservabilityService,
    legacy_reply_aware_observability,
    WorkerQueueTelemetryService,
    resolve_pipeline_observability,
)
from stream_kernel.platform.services.observability_dispatch import (
    DispatchingObservabilityService,
)
from stream_kernel.platform.services.messaging.reply_waiter import (
    InMemoryReplyWaiterService,
    PendingReplyWaiterService,
    ReplyWaiterRegistryStore,
    ReplyWaiterService,
    TerminalEvent,
)
from stream_kernel.platform.services.messaging.reply_coordinator import (
    InMemoryReplyCoordinatorService,
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.runtime.lifecycle import (
    LocalRuntimeLifecycleManager,
    LocalExecutionWorkerLifecycleService,
    ExecutionWorkerLifecycleService,
    ExecutionWorkerHandle,
    ExecutionWorkerRegistry,
    RuntimeLifecycleManager,
)
from stream_kernel.platform.services.runtime.transport import (
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)
from stream_kernel.platform.services.runtime.async_dispatch_loop import AsyncDispatchLoop
from stream_kernel.platform.services.runtime.process_group_router import (
    InMemoryProcessGroupRouterService,
    ProcessGroupRouterStore,
    ProcessGroupRouterService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneEventStore,
    ControlPlaneStateService,
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryRegistry,
    ControlPlaneDiscoveryService,
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
    ControlPlaneDiscoveryAdapter,
    DefaultControlPlaneDiscoveryAdapter,
    DefaultControlPlaneBootstrapperService,
    control_plane_discovery_adapter,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneConfigStreamAdapter,
    ControlPlaneConfigStreamService,
    ControlPlaneStartupConfigRegistry,
    ControlPlaneStartupConfigStore,
    DefaultControlPlaneConfigStreamService,
    InMemoryControlPlaneStartupConfigStore,
    YamlControlPlaneConfigStreamAdapter,
    control_plane_config_stream_adapter,
)
from stream_kernel.platform.services.runtime.control_plane_config_apply import (
    ControlPlaneAppliedConfigRegistry,
    ControlPlaneAppliedConfigStore,
    ControlPlaneSystemConfigApplyService,
    ControlPlaneObservabilityConfigApplyService,
    ControlPlaneNodeConfigApplyService,
    ControlPlaneConfigApplyProgress,
    ControlPlaneConfigApplyTrackerService,
    DefaultControlPlaneSystemConfigApplyService,
    DefaultControlPlaneObservabilityConfigApplyService,
    DefaultControlPlaneNodeConfigApplyService,
    InMemoryControlPlaneAppliedConfigStore,
    InMemoryControlPlaneConfigApplyTrackerService,
    resolve_expected_config_apply_counts,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
    InMemoryControlPlaneStartupBarrierService,
)
from stream_kernel.platform.services.runtime.control_plane_launch_plan import (
    ControlPlaneLaunchPlanService,
    DefaultControlPlaneLaunchPlanService,
)
from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    ControlPlaneDagAssemblyService,
    DefaultControlPlaneDagAssemblyService,
)


_IPC_EXPORT_NAMES = (
    "ExecutionIpcCodec",
    "ExecutionIpcCodecError",
    "ExecutionIpcControlSignal",
    "ExecutionIpcEndpointRegistry",
    "ExecutionIpcFlowControlPolicy",
    "ExecutionIpcKvStreamPort",
    "ExecutionIpcMessage",
    "ExecutionIpcPort",
    "ExecutionIpcReceivePolicy",
    "ExecutionIpcTransportService",
    "ExecutionIpcTransportCoordinatorService",
    "CreditWindowFlowControlPolicy",
    "HybridFlowControlPolicy",
    "InMemoryExecutionIpcTransportAdapter",
    "NoopFlowControlPolicy",
    "PipeExecutionIpcTransportAdapter",
    "TokenBucketFlowControlPolicy",
    "resolve_execution_ipc_flow_control",
)


def _try_load_ipc_exports() -> None:
    try:
        from stream_kernel.execution.transport import ipc as ipc_mod
    except Exception:
        return
    for name in _IPC_EXPORT_NAMES:
        if name in globals():
            continue
        try:
            globals()[name] = getattr(ipc_mod, name)
        except Exception:
            continue


def __getattr__(name: str):
    if name in _IPC_EXPORT_NAMES:
        _try_load_ipc_exports()
        if name in globals():
            return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ConsumerRegistry",
    "ConsumerRegistryStore",
    "ApiPolicyService",
    "ContextService",
    "DiscoveryConsumerRegistry",
    "InMemoryApiPolicyService",
    "InMemoryOutboundApiService",
    "InMemoryKvContextService",
    "InMemoryRateLimiterService",
    "LocalRuntimeLifecycleManager",
    "LocalExecutionWorkerLifecycleService",
    "ExecutionWorkerLifecycleService",
    "ExecutionWorkerHandle",
    "ExecutionWorkerRegistry",
    "NoOpObservabilityService",
    "DispatchingObservabilityService",
    "InMemoryObservabilityMetricsService",
    "DefaultWorkerQueueTelemetryService",
    "coerce_pipeline_observability",
    "ObservabilityMetricsService",
    "ObservabilityPipelineService",
    "ObservabilityService",
    "WorkerQueueTelemetryService",
    "ReplyAwareObservabilityService",
    "InMemoryReplyWaiterService",
    "InMemoryReplyCoordinatorService",
    "PendingReplyWaiterService",
    "ReplyWaiterRegistryStore",
    "ReplyCoordinatorService",
    "ReplyWaiterService",
    "RateLimiterService",
    "OutboundApiService",
    "OutboundCircuitOpenError",
    "OutboundRateLimitedError",
    "RuntimeLifecycleManager",
    "RuntimeTransportService",
    "TerminalEvent",
    "legacy_reply_aware_observability",
    "resolve_pipeline_observability",
    "legacy_reply_coordinator",
    "MemoryRuntimeTransportService",
    "InMemoryProcessGroupRouterService",
    "ProcessGroupRouterStore",
    "ProcessGroupRouterService",
    "TcpLocalRuntimeTransportService",
    "AsyncDispatchLoop",
    "ControlPlaneEventStore",
    "ControlPlaneStateService",
    "InMemoryControlPlaneStateService",
    "ControlPlaneDiscoveryRegistry",
    "ControlPlaneDiscoveryService",
    "InMemoryControlPlaneDiscoveryService",
    "ControlPlaneBootstrapperService",
    "ControlPlaneDiscoveryAdapter",
    "DefaultControlPlaneDiscoveryAdapter",
    "DefaultControlPlaneBootstrapperService",
    "control_plane_discovery_adapter",
    "ControlPlaneConfigStreamAdapter",
    "ControlPlaneConfigStreamService",
    "ControlPlaneStartupConfigRegistry",
    "ControlPlaneStartupConfigStore",
    "DefaultControlPlaneConfigStreamService",
    "InMemoryControlPlaneStartupConfigStore",
    "YamlControlPlaneConfigStreamAdapter",
    "control_plane_config_stream_adapter",
    "ControlPlaneAppliedConfigRegistry",
    "ControlPlaneAppliedConfigStore",
    "ControlPlaneSystemConfigApplyService",
    "ControlPlaneObservabilityConfigApplyService",
    "ControlPlaneNodeConfigApplyService",
    "ControlPlaneConfigApplyProgress",
    "ControlPlaneConfigApplyTrackerService",
    "DefaultControlPlaneSystemConfigApplyService",
    "DefaultControlPlaneObservabilityConfigApplyService",
    "DefaultControlPlaneNodeConfigApplyService",
    "InMemoryControlPlaneAppliedConfigStore",
    "InMemoryControlPlaneConfigApplyTrackerService",
    "resolve_expected_config_apply_counts",
    "ControlPlaneStartupBarrierService",
    "InMemoryControlPlaneStartupBarrierService",
    "ControlPlaneLaunchPlanService",
    "DefaultControlPlaneLaunchPlanService",
    "ControlPlaneDagAssemblyService",
    "DefaultControlPlaneDagAssemblyService",
    "kv_store_memory",
    "ExecutionIpcCodec",
    "ExecutionIpcCodecError",
    "ExecutionIpcControlSignal",
    "ExecutionIpcEndpointRegistry",
    "ExecutionIpcFlowControlPolicy",
    "ExecutionIpcKvStreamPort",
    "ExecutionIpcMessage",
    "ExecutionIpcPort",
    "ExecutionIpcReceivePolicy",
    "ExecutionIpcTransportService",
    "ExecutionIpcTransportCoordinatorService",
    "NoopFlowControlPolicy",
    "CreditWindowFlowControlPolicy",
    "TokenBucketFlowControlPolicy",
    "HybridFlowControlPolicy",
    "resolve_execution_ipc_flow_control",
    "InMemoryExecutionIpcTransportAdapter",
    "PipeExecutionIpcTransportAdapter",
]
