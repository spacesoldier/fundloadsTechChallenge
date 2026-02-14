# Multiprocess bus ownership and outbound route cache

## Context

Current runtime has two distinct realities:

1. control/lifecycle path is implemented and stable in process-supervisor mode;
2. data-plane isolation is still transitional: cross-group workload is currently
   orchestrated through supervisor-mediated boundary calls.

This note freezes a target model where:

- control plane remains star-shaped through supervisor-managed channels;
- main business traffic does not flow through supervisor;
- workers exchange data through a dedicated inter-process bus;
- router/routing service cache outbound "outside-process" paths to reduce lookup cost.

---

## Current state (as of Phase 5pre Step H)

### What is implemented

- Multiprocess worker lifecycle (`start_groups`, `wait_ready`, `stop_groups`) with
  deterministic fallback (`force_terminate_groups`) in:
  `src/stream_kernel/platform/services/bootstrap.py`.
- Process-group placement by node name (`runtime.platform.process_groups[].nodes`)
  and multi-hop boundary dispatch in supervisor.
- Reply correlation and terminal completion via `ReplyCoordinatorService`.
- Execution tracing observers and exporter contracts (`jsonl`, `stdout`,
  `otel_otlp`, `opentracing_bridge`).

### What is not implemented yet

- Dedicated data bus backend (for example ZeroMQ) for worker-to-worker traffic.
- Full OTLP network exporter implementation (current OTLP sink maps span payloads;
  network delivery path is still a follow-up item).

### What is implemented now (February 14, 2026)

- Supervisor-side outbound route cache for boundary dispatch:
  - positive cache (`target + source_group -> dispatch_group`);
  - optional negative cache for unresolved targets;
  - invalidation on placement-map reconfiguration;
  - deterministic diagnostics via `route_cache_snapshot()`.
- Runtime contract for route cache tuning:
  - `runtime.platform.routing_cache.enabled`;
  - `runtime.platform.routing_cache.negative_cache`;
  - `runtime.platform.routing_cache.max_entries`.

### Recently closed (February 13, 2026)

- Child workers now rebuild runtime graph-native source/sink wrappers from bundle
  adapter config (`source:*` / `sink:*`), so boundary execution can start from
  source bootstrap nodes in multiprocess mode.
- Child boundary loop now routes non-targeted outputs through `RoutingService`
  (explicit targets still pass through as-is), which keeps routing semantics aligned
  with runner behavior.
- Cross-process trace markers are now propagated as route metadata:
  `process_group`, `handoff_from`, `route_hop`.

---

## Target runtime topology

## 1) Control plane (keep star topology)

- Owner: supervisor process.
- Channels: process-start bootstrap, readiness/heartbeat, stop/ack, failure
  diagnostics.
- Requirement: deterministic lifecycle and graceful shutdown semantics.

Control plane stays star-shaped by design because:

- state machine ownership must be centralized;
- failure policy is easier to reason about from one supervisor source of truth;
- shutdown and readiness gates are process-global concerns.

## 2) Data plane (move off supervisor path)

- Owner: dedicated bus service lifecycle, started/stopped by supervisor.
- Primary pattern: workers publish/consume through bus topics/queues.
- Supervisor does not proxy business payloads in steady state.

The supervisor remains owner of bus lifecycle, not owner of per-message data flow.

---

## Bus ownership decision

Chosen ownership model for local multiprocess profile:

- `supervisor-owned bus lifecycle`:
  - supervisor starts bus endpoints/broker adapter during startup;
  - workers receive bus connection parameters through bootstrap metadata;
  - supervisor stops bus endpoints on graceful/forced stop.

Rationale:

- one place for lifecycle policy and diagnostics;
- no hidden external dependency for local developer runs;
- predictable startup ordering (bus ready before workers become ready).

Future extension:

- external bus mode (`managed_externally=true`) can be introduced later for
  Kubernetes/managed deployments.

---

## Routing model with outbound cache

## 1) Lookup order

For each routed payload:

1. resolve local consumers in current process/group;
2. if local route exists, deliver locally;
3. if local route missing, resolve boundary destination(s) through placement map;
4. publish to bus topic/queue for target process group.

## 2) Cache layers

Routing service keeps two caches:

- local route cache:
  key: `(token_type, source_node, process_group)`
  value: `list[target_node]`
- outbound bus route cache:
  key: `(token_type, process_group)`
  value: `list[target_group_or_topic]`

Plus explicit target cache:

- key: `(target_node)`
- value: `(target_group, bus_address/topic)`

## 3) Invalidation

Cache validity is version-bound:

- consumer registry version;
- placement registry version.

Effective cache key suffix:

- `(consumer_version, placement_version)`.

Optional negative cache is allowed (no route found), with same version binding.

## 4) Expected effect

- lower per-message routing overhead under stable topology;
- no repeated placement lookups for hot tokens;
- deterministic invalidation when topology changes.

---

## Configuration shape (target, not fully implemented yet)

```yaml
runtime:
  platform:
    execution_ipc:
      transport: zeromq_local
      bind_host: 127.0.0.1
      bind_port: 5555
      auth:
        mode: hmac
        secret_mode: generated
        kdf: hkdf_sha256
        ttl_seconds: 30
        nonce_cache_size: 100000
      max_payload_bytes: 1048576
    data_bus:
      backend: zeromq
      owner: supervisor
      mode: brokered
      endpoints:
        publish: tcp://127.0.0.1:5601
        subscribe: tcp://127.0.0.1:5602
    routing_cache:
      enabled: true
      negative_cache: true
      max_entries: 100000
```

Note:

- current validator/execution supports `tcp_local` profile;
- `zeromq_local` and `data_bus.*` are the next-phase contract items.

---

## Observability flow in target model

- worker emits tracing/telemetry/logging events to observability stream port;
- observability adapters publish events to supervisor-owned observability channel;
- supervisor performs final export fan-out (OTLP/bridge/jsonl/stdout).

This keeps export credentials and exporter lifecycle centralized.

---

## Jaeger transport note

- Prefer OTLP path (`4317` gRPC / `4318` HTTP) via OTel collector -> Jaeger.
- Jaeger UDP ingestion (`6831/6832`) remains legacy-compatible.
- Host can send UDP to Jaeger container if UDP ports are published (`-p .../udp`),
  but OTLP is the primary recommended path.

---

## Immediate implementation follow-up

1. Freeze validator/runtime contract for `data_bus` and routing-cache sections.
2. Extend route cache from supervisor boundary path to bus-topic path once data bus backend lands.
3. Introduce bus adapter contract (`queue/topic`) for worker data plane.
4. Keep star control plane untouched for lifecycle and diagnostics.
