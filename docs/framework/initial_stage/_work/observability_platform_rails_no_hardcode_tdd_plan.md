# Observability platform rails without hardcode (TDD subplan)

## Goal

Move tracing/telemetry/monitoring/logging to fully platform-native rails:

- no direct factory wiring in runtime hot paths;
- no backend-specific branching outside adapter/service boundaries;
- system nodes + platform services for observability flow orchestration;
- deterministic sync/async execution selection through DI metadata.

## Status

- [x] Phase A complete:
  - pipeline contract is frozen in validator via `runtime.observability.pipeline.*`;
  - mixed declarations (`pipeline` + legacy tracing/logging exporters) are rejected;
  - unknown system observability node kinds are rejected;
  - both `tracing_only` and `full_multi_stream` pipeline profiles are validated.
- [x] Phase B complete:
  - tracing observer sink resolution is DI/registry-only (direct factory fallback removed);
  - strict mode fails fast on missing exporter adapter binding;
  - non-strict mode skips missing bindings;
  - runtime builder and child bootstrap now materialize observability exporter adapters via `AdapterRegistry`.
- [x] Phase C complete:
  - detailed TDD spec: [observability_platform_rails_phasec_service_unification_tdd_spec](observability_platform_rails_phasec_service_unification_tdd_spec.md)
  - unified platform callbacks are now routed via `ObservabilityPipelineService`;
  - ingress/outbound/lifecycle callback call-sites use platform service API;
  - compatibility adapter keeps partial/legacy callback providers deterministic.
- [x] Phase D complete:
  - detailed TDD spec: [observability_platform_rails_phased_system_nodes_tdd_spec](observability_platform_rails_phased_system_nodes_tdd_spec.md)
  - `runtime.observability.pipeline.system_nodes` are materialized into runtime system nodes;
  - dispatch nodes are registered as typed consumers and included in child bootstrap path;
  - qualifier-aware DI markers propagate async capability into execution pool planning.
- [x] Phase E complete:
  - detailed TDD spec: [observability_platform_rails_phasee_dependency_groups_and_availability_tdd_spec](observability_platform_rails_phasee_dependency_groups_and_availability_tdd_spec.md)
  - startup dependency diagnostics for OTLP backends are deterministic;
  - explicit `dependency_missing=degrade_noop` path is supported and validated;
  - Poetry optional dependency groups are defined for observability backend families.
- [x] Phase F complete:
  - detailed TDD spec: [observability_platform_rails_phasef_perf_isolation_gate_tdd_spec](observability_platform_rails_phasef_perf_isolation_gate_tdd_spec.md)
  - optional fan-out callback failures are isolated per observer;
  - perf/parity report committed:
    [observability_platform_rails_phasef_perf_parity_report](observability_platform_rails_phasef_perf_parity_report.md).
- [x] Phase G complete:
  - detailed TDD spec: [observability_platform_rails_phaseg_documentation_closure_tdd_spec](observability_platform_rails_phaseg_documentation_closure_tdd_spec.md)
  - migration closure report committed:
    [observability_platform_rails_phaseg_docs_closure_report](observability_platform_rails_phaseg_docs_closure_report.md);
  - architecture docs and `_work` index synchronized.

## Historical starting gaps (closed by phases A-G)

Initial implementation had exporter backends and async runner support, but still contained transitional seams:

1. tracing exporters were partially built via direct factory map in observer code;
2. telemetry/monitoring adapters existed, but runtime pipeline was tracing-centric;
3. dependency contract for optional backend libraries was not yet formalized in Poetry groups;
4. outbound policy observability used callback-style hooks and had to converge to unified pipeline service API.

## Target model

### 1) Contracts

- `ObservabilityPipelineService` (platform service):
  - `publish_trace(event)`
  - `publish_log(event)`
  - `publish_metric(event)`
  - `publish_monitoring(event)`
- `ObservabilitySinkPort` (platform port family):
  - typed sink contracts per stream (`trace/log/metric/monitoring`)
  - async capability advertised via adapter metadata only.

### 2) System nodes

- Introduce graph-delegatable system nodes:
  - `system.obs.trace_dispatch`
  - `system.obs.log_dispatch`
  - `system.obs.metric_dispatch`
  - `system.obs.monitor_dispatch`
- These nodes are optional and profile-driven, but always run through the same runner/DI rails as business nodes.

### 3) Adapter/service boundaries

- all transport/library specifics stay in adapters (`urllib/requests/httpx/aiohttp/urllib3/grpcio/otel_sdk`);
- platform services orchestrate policy/batching/retry/diagnostics;
- runners never import transport libraries directly.

### 4) Dependency policy

- optional Poetry groups per backend family;
- deterministic startup diagnostics when configured backend is unavailable.

## TDD phases

### Phase A — contract freeze + config alignment

RED:

- reject mixed/invalid observability pipeline declarations;
- reject unknown system observability node kinds;
- ensure config can choose tracing-only or full multi-stream pipeline.

GREEN:

- validator/model freeze for unified `runtime.observability.pipeline.*`.

### Phase B — DI-first sink resolution (remove factory hardcode)

RED:

- observer/runtime must fail if sink binding is missing in strict mode;
- observer/runtime must use DI/registry resolution only (no direct factory map path).

GREEN:

- replace direct sink factory map in tracing observer with DI-resolved sink services/adapters.

### Phase C — observability platform service unification

RED:

- tracing/logging/telemetry/monitoring events pass through single platform service contract;
- callbacks (`on_outbound_policy_decision`, ingress limiter hooks, lifecycle hooks) are routed through the unified service.

GREEN:

- platform `ObservabilityPipelineService` implemented and wired.

### Phase D — system nodes rollout

RED:

- system observability nodes are discovered and schedulable via process groups;
- async-only sinks force async execution rail through dependency propagation.

GREEN:

- system nodes added for dispatch stages, with deterministic no-op fallback when disabled.

### Phase E — dependency groups + backend availability diagnostics

RED:

- config backend selected but package missing -> deterministic startup error (or explicit degrade mode if configured);
- dependency matrix test for all supported backends.

GREEN:

- Poetry optional groups added:
  - `observability-http`
  - `observability-async-http`
  - `observability-grpc`
  - `observability-otel-sdk`
  - `observability-all`

### Phase F — performance + isolation gate

RED:

- exporter failure isolation preserved under multi-stream fanout;
- no blocking regression in sync baseline when async pipeline is disabled.

GREEN:

- perf/parity report for:
  - tracing-only
  - tracing+logging
  - full observability fanout

### Phase G — documentation and migration closure

RED:

- docs references still mention transitional hardcoded factory path.

GREEN:

- architecture docs and `_work` index synchronized;
- old shim notes removed or explicitly marked legacy.

## Acceptance criteria

- runner hot path contains no transport-library branching;
- all observability backends are activated through platform adapters/services only;
- system observability nodes can be delegated to dedicated async process groups;
- strict-mode diagnostics are deterministic for misconfiguration/missing dependencies;
- regression parity for baseline/experiment scenarios is preserved.

## Cross-plan synchronization

This subplan depends on and extends:

- [observability_exporters_and_async_runner_master_tdd_plan](observability_exporters_and_async_runner_master_tdd_plan.md)
- [lifecycle_logging_async_pipeline_tdd_plan](lifecycle_logging_async_pipeline_tdd_plan.md)
- [platform_api_services_and_rate_limiter_policies_tdd_plan](platform_api_services_and_rate_limiter_policies_tdd_plan.md)
- [network_interfaces_expansion_plan](network_interfaces_expansion_plan.md)

It also defines prerequisites for upcoming FastAPI phases where outbound/inbound observability must use the same platform rails.
