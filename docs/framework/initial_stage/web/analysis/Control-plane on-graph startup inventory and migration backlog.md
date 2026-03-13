# Control-plane on-graph startup inventory and migration backlog

## Scope

This note captures:

1. Which system nodes already implement the startup path (`discovery -> config -> bootstrap`) in graph form.
2. What is still executed off-graph in `builder/planning`.
3. Estimated migration scale to complete graph-native startup.

## Root: current on-graph startup path

### Discovery and config stream

- `system.cp.root_bootstrap` -> emits `ControlPlaneDiscoveryStartRequestedEvent`.
- `system.cp.consumer_registry_bindings_apply` -> applies startup consumer bindings (source/sink maps) via dynamic registry service.
- `system.cp.root_config_stream` -> emits config records from `ControlPlaneConfigStreamService`.
- `system.cp.discovery_pump` -> drives discovery stream batches.
- `system.cp.discovery_apply` -> appends discovery entities into `ControlPlaneDiscoveryService`.
- `system.cp.discovery_materialize` -> materializes discovered entities.
- `system.cp.consumer_registry_discovery_apply` -> dynamically registers node consumers from discovery records.
- `system.cp.discovery_finalize` -> discovery completion stage.

### Config apply and startup barrier

- `system.cp.system_config_apply`
- `system.cp.observability_config_apply`
- `system.cp.node_config_apply`
- `system.cp.config_apply_barrier` -> waits for full config apply completion.
- `system.cp.startup_barrier` -> gates startup on both discovery + config completion.

### Launch/bootstrap orchestration

- `system.cp.dag_assembly` -> builds `ControlPlaneLaunchPlan`.
- `system.cp.init_plan` -> emits `ControlPlaneLaunchPlanEvent`, `ControlPlaneInitEvent`, `ControlPlaneSpawnRequestedEvent`.
- `system.cp.consumer_registry_group_prune` -> emits `ControlPlaneConsumerRegistryRemoveNodesEvent` for plan nodes.
- `system.cp.consumer_registry_remove` -> removes stale consumer bindings.
- `system.cp.shutdown_expected_groups` -> configures expected shutdown quorum.

### Root runtime control path after bootstrap

- `system.cp.start_work_readiness`
- `system.cp.start_work_dispatch`
- `system.cp.start_work_command_dispatch`
- `system.cp.shutdown_leaf_ready`
- `system.cp.root_stop`

## Leaf: current on-graph startup path

### Bootstrap and ingress

- `system.cp.leaf_bootstrap` -> emits leaf hello + scheduler upsert commands.
- `system.cp.consumer_registry_bindings_apply` -> applies startup consumer bindings (source/sink maps) via dynamic registry service.
- `source:system.cp.leaf_command_ingress:*` nodes -> IPC lane sources per lane.

### Discovery/config apply

- `system.cp.leaf_discovery` (request-based flow compatibility).
- `system.cp.leaf_snapshot_apply` (snapshot protocol primary flow).
- `system.cp.consumer_registry_discovery_apply` -> dynamic consumer registration in leaf.
- `system.cp.consumer_registry_remove` -> dynamic consumer removal in leaf.
- `system.cp.leaf_apply_config`.

### Runtime execution and stop path

- `system.cp.leaf_start_work`
- `system.cp.leaf_boundary_execute`
- `system.cp.leaf_tombstone_finalize`
- `system.cp.leaf_stop`
- `system.cp.leaf_reply_dispatch`

## Off-graph pieces still present

## 1) Build-time module discovery and graph construction (high impact)

Location: `execution/orchestration/builder.py`

- `load_discovery_modules(...)`
- `ApplicationContext.discover(...)`
- `ctx.preflight(...)`
- `ctx.build_scenario(...)`

Reason still off-graph:
- This currently creates the executable graph itself (node metadata, DAG contracts, scenario object).
- Full on-graph migration requires runtime graph mutation API (add/remove nodes/edges/contracts) plus deterministic re-wiring guarantees.

Estimated scale: **L** (architecture-level change, many tests).

## 2) Source/sink runtime node synthesis from adapters (medium-high impact)

Location: `builder.py`

- `build_source_ingress_plan(...)`
- `build_sink_runtime_nodes(...)`

Current state:
- Direct off-graph consumer registration for source/sink maps was removed from `builder`.
- Source/sink consumer maps are now applied on-graph via `ControlPlaneConsumerRegistryBindingsApplyEvent`
  consumed by `system.cp.consumer_registry_bindings_apply`.
- Control-plane / observability / debug consumer maps are also applied via the same bootstrap event.
- Startup input order now guarantees binding apply event precedes root/leaf pulse event.

Reason still off-graph:
- Adapter inventory is still turned into runnable source/sink steps before runner start.
- Full graph-native migration still requires "descriptor events -> node factory service -> graph mutation".

Estimated scale: **M-L** (new mutation flow + startup protocol changes).

## 3) DI default bindings and transport wiring (medium impact)

Location: `builder.py`

- `ensure_runtime_registry_bindings(...)`
- `ensure_runtime_transport_bindings(...)`
- `ensure_runtime_ipc_bindings(...)`
- `ensure_runtime_lifecycle_bindings(...)`

Reason still off-graph:
- This is currently process bootstrap wiring before first runner cycle.
- Can be reduced but not fully removed unless DI container itself supports in-graph mutation and late binding.

Estimated scale: **M**.

## 4) Initial pulse injection into runner input (low impact)

Location: `control_plane_bootstrap_inputs(...)` in control-plane planning.

Reason still off-graph:
- One initial seed event is inserted before runner loop (`ControlPlaneRootPulse` / `ControlPlaneLeafPulse`).
- This is already minimal and deterministic. Can remain as the only off-graph seed.

Estimated scale to change further: **S** (optional).

## Migration backlog (ordered)

1. Keep only a single off-graph seed input (`RootPulse` / `LeafPulse`) and treat all other startup as graph events.
2. Introduce graph mutation service contract (node/service/store) for dynamic source/sink registration.
3. Move adapter-based source/sink synthesis into event-driven graph mutation path.
4. Keep build-time static discovery only for framework bootstrap minimum; project/runtime expansion should flow through discovery stream events.
5. Collapse remaining builder startup assembly helpers after graph mutation path is stable.
