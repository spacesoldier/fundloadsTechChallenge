# Observability exporter grouped config + backend selection (TDD plan)

## Goal

Make OTLP exporter backend selection deterministic from config and split exporter settings into grouped sections without breaking flat backward compatibility.

Target behavior:

- backend is resolved from exporter contract, not accidentally defaulted to `urllib`;
- grouped settings are supported (`otlp`, `transport`, `service`, `view`);
- flat settings remain valid;
- builder forwards resolved backend to adapter factories;
- adapter can consume grouped config directly.

## Contract

Backend resolution order (`otel_otlp*`):

1. `runtime.observability.tracing.exporters[i].backend`
2. `runtime.observability.tracing.exporters[i].settings.transport.backend`
3. `runtime.observability.tracing.exporters[i].settings.backend` (compat)
4. default `urllib`

Grouped settings flattening (compat overlay):

- `settings.otlp.endpoint -> settings.endpoint`
- `settings.otlp.headers -> settings.headers`
- `settings.transport.{timeout_seconds,dependency_missing,bridge,batch,queue,retry,httpx,grpc,urllib3,aiohttp} -> flat keys`
- `settings.service.{service_name,service_namespace,service_version,service_instance_id,deployment_environment} -> flat keys`
- `settings.view.{trace_view,service_name_by_step,service_name_by_process_group,service_name_suffix,isolate_view_ids,include_runtime_resource,span_kind} -> flat keys`

## Phases

### Phase A — Validator contract (RED/GREEN)

- RED:
  - `test_validate_newgen_config_obs_cfg_a_04c_accepts_transport_group_backend`
  - `test_validate_newgen_config_obs_cfg_a_04d_accepts_settings_backend_for_compat`
- GREEN:
  - grouped settings flatten in validator;
  - backend normalized into `exporter.backend` using contract order.

Status: done.

### Phase B — Runtime builder backend forwarding (RED/GREEN)

- RED:
  - `test_build_runtime_observability_adapter_instances_resolves_backend_from_transport_group`
- GREEN:
  - builder resolves backend from exporter/transport/settings and forwards to adapter settings.

Status: done.

### Phase C — Adapter grouped parsing (RED/GREEN)

- RED:
  - `test_trace_otel_otlp_accepts_grouped_settings_and_resolves_backend`
  - `test_trace_otel_otlp_transport_group_backend_overrides_settings_backend`
- GREEN:
  - `trace_otel_otlp` accepts grouped settings and resolves backend with transport override.

Status: done.

### Phase D — Regression guard

- Run focused suite:
  - validator targeted tests for OBS-CFG-A-04C/04D
  - builder targeted backend forwarding test
  - adapter grouped-config tests

Status: done.

## Notes

- This subplan is a closure patch for backend mis-resolution observed on Jaeger configs where backend lived inside exporter `settings`.
- Flat config remains valid; grouped config is additive and preferred.
