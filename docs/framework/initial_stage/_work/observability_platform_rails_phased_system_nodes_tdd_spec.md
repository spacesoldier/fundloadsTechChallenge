# Observability platform rails Phase D (system nodes rollout)

## Scope

Implement graph-delegatable system observability nodes from
`runtime.observability.pipeline.system_nodes` on the same runtime rails as regular nodes.

## RED (contract tests)

1. Enabled system nodes must be materialized as runtime `StepSpec` entries and registered as
   consumers for stream-specific dispatch payloads:
   - `system.obs.trace_dispatch` -> `TraceDispatchEvent`
   - `system.obs.log_dispatch` -> `LogDispatchEvent`
   - `system.obs.metric_dispatch` -> `MetricDispatchEvent`
   - `system.obs.monitor_dispatch` -> `MonitorDispatchEvent`
2. Disabled system nodes must not be added to scenario/runtime wiring.
3. Node qualifier from config must be reflected in DI marker metadata so `plan_pools(...)`
   can infer async execution when the qualified `ObservabilityPipelineService` binding is async.
4. Runtime execution must remain deterministic if an observability binding is missing:
   system dispatch falls back to no-op pipeline service.

## GREEN (implementation)

1. Added runtime builder module:
   - `src/stream_kernel/execution/orchestration/observability_system_nodes.py`
   - `build_observability_system_plan(...)`
   - stream-specific dispatch payload models
   - `ObservabilityDispatchNode` with qualifier-aware DI marker
2. Wired system-node plan into scenario assembly:
   - `src/stream_kernel/execution/orchestration/builder.py`
   - `src/stream_kernel/execution/orchestration/child_bootstrap.py`
3. Extended `ObservabilityPipelineService` with stream publish methods:
   - `publish_trace`
   - `publish_log`
   - `publish_metric`
   - `publish_monitoring`
4. Implemented publish methods across platform services:
   - `NoOpObservabilityService`
   - `FanoutObservabilityService`
   - `ReplyAwareObservabilityService`
   - compatibility `_PipelineObservabilityAdapter`

## Tests

- `tests/stream_kernel/execution/orchestration/test_builder.py`
  - `test_build_observability_system_plan_builds_enabled_nodes_and_consumers`
  - `test_build_observability_system_plan_async_qualifier_propagates_to_pool_planning`
- `tests/stream_kernel/platform/services/test_observability_pipeline_service.py`
  - publish-method coverage for no-op, fanout and reply-aware wrappers
- Regression sanity:
  - `tests/stream_kernel/execution/orchestration/test_child_bootstrap.py`
  - `tests/stream_kernel/execution/runtime/test_execution_planning.py`

## Notes

- System nodes are runtime-assembled (builder/child bootstrap) and therefore process-group
  schedulable by node name like other runtime nodes.
- Current fallback policy is deterministic no-op when service binding is absent for configured
  qualifier.
