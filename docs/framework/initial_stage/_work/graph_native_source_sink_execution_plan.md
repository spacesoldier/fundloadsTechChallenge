# Graph-Native Source/Sink Execution Plan

## Goal

Remove remaining runtime special paths and execute source/sink adapters on the same rails as regular nodes.

## Scope

- Eliminate bootstrap marker payload hacks.
- Move source bootstrap to regular runner input path.
- Keep deterministic one-by-one source emission.
- Prepare full migration away from `source_nodes/bootstrap_targets` special wiring.

## Stages

- [x] Stage 0: Baseline guard rails
  - Keep `tests/integration` and `tests/stream_kernel` green before refactors.
- [x] Stage 1: Clean bootstrap control path
  - Replace `run_bootstrap_targets` runtime path usage with `run_inputs` + targeted `Envelope`.
  - Remove marker payload (`__bootstrap_target`) from bootstrap control flow.
  - Add tests proving source node receives clean typed control payload (`BootstrapControl`).
- [x] Stage 2: Source as graph-native runtime nodes
  - Remove `RuntimeBuildArtifacts.source_nodes` and `RuntimeBuildArtifacts.bootstrap_targets`.
  - Ensure source nodes are part of regular scenario execution set.
- [x] Stage 3: Sink as graph-native runtime nodes
  - Remove project-level sink step hard dependency for output write path.
  - Route sink behavior via adapter contracts and runtime sink wrappers.
- [x] Stage 4: Remove hardcoded IO adapter names
  - Remove `input_source`/`output_sink` hardcoding from CLI overrides.
  - Remove `adapters.output_sink` special validation path.
- [x] Stage 5: Final cleanup
  - Delete dead bootstrap helper APIs and their tests.
  - Re-run full regression suite and update docs.

## Current Notes

- Stage 1, Stage 2, Stage 3, Stage 4 and Stage 5 completed.
- Source bootstrap now self-schedules via targeted `BootstrapControl` envelopes on regular routing rails.
- Sink adapters (`consumes!=[]`, `emits=[]`) are attached as runtime sink nodes when the consumed token has no in-graph consumer.
- CLI I/O path overrides are now generic:
  - explicit role via `runtime.cli.input_adapter` / `runtime.cli.output_adapter`, or
  - auto-resolved from discovered adapter contracts (`consumes`/`emits`).
- Config validation now checks all `adapters.<role>` entries uniformly (no `output_sink` special case).
- Build-time helper surface in execution builder is reduced:
  - removed standalone scenario/consumer merge helper APIs that were only internally used,
  - source bootstrap targets are derived directly from source runtime node names.
