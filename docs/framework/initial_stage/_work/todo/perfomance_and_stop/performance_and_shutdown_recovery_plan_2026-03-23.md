# Performance and Shutdown Recovery Plan (2026-03-23)

## Scope and Goal

This plan is based on `tombstone_finalize_signal_conflicts.md` updates from **Update 7 (2026-03-22)** and later post-session analysis.

Primary goal:
- restore stable throughput close to pre-regression baseline;
- make shutdown deterministic (no timeout-kill of worker processes);
- remove ambiguity between control-plane lifecycle and data-plane tombstone propagation.

Target outcomes:
- 1000-record scenario finishes data processing and coordinated shutdown within configured timeout;
- no orphan/leaked runtime processes after run;
- startup handshake completes for all required business groups;
- shutdown chain is observable and diagnosable from logs/debug artifacts.

---

## Current Facts (from latest analysis)

1. **H-1 remains unresolved**: business workers often do not complete CP handshake (`DiscoveryAck`/`ConfigAck` not observed in root).
2. **Throughput regression appeared after R-1 wave**: hot-path runner and CP routing changes likely added overhead and/or startup gating effects.
3. **Shutdown still fragile**: stop chain depends on multiple conditions; partial completion leads to timeout-based kill.
4. **5-process e2e may pass while 10-process fails**: indicates scale-sensitive propagation/backpressure/race behavior.

---

## Non-Negotiable Architecture Constraints

1. Do not move lifecycle logic into ad-hoc off-graph calls.
2. Do not add new logic into runner hot loop unless profile proves necessity.
3. Keep control-plane completion independent from accidental data-plane “last message” assumptions.
4. Preserve deterministic output semantics and explicit observability.

---

## Workstreams and Phases

## Phase 0 — Freeze and Instrumentation Baseline (Day 0)

Purpose:
- stabilize investigation conditions before additional design changes.

Actions:
1. Lock one canonical experiment config for recovery loop.
2. Capture baseline artifacts for each run:
- root log
- per-worker log
- debug events (if enabled)
- output count and processing time
- process postcheck (remaining pids)
3. Add a run sheet table for every trial:
- commit hash
- config hash
- elapsed total
- business records processed
- shutdown completed yes/no
- reason of failure

Acceptance criteria:
- every next attempt is comparable with same measurement fields.

---

## Phase 1 — Fix H-1 Handshake Path First (Highest Priority)

Purpose:
- guarantee `Hello -> DiscoverySnapshot -> DiscoveryAck -> ConfigCard -> ConfigAck -> StartupReady` for all business groups.

Likely touchpoints:
- `src/stream_kernel/execution/orchestration/control_plane/leaf/plan_builder.py`
- `src/stream_kernel/execution/orchestration/control_plane/root/plan_builder.py`
- `src/stream_kernel/execution/orchestration/control_plane/leaf/system_nodes.py`
- `src/stream_kernel/execution/orchestration/control_plane/root/system_nodes.py`
- reply dispatch path in leaf runtime lifecycle.

Actions:
1. Add narrow diagnostics at CP event boundaries:
- event emitted,
- route target resolved,
- dispatch accepted/rejected,
- event received by root sink node.
2. Validate routing table for these event types explicitly:
- `ControlPlaneLeafDiscoveryAckEvent`
- `ControlPlaneLeafConfigAckEvent`
- `ControlPlaneLeafHelloEvent`
3. Confirm lane correctness per worker (control lane only for these events).
4. Add/extend tests:
- unit: routing map contains required CP types;
- e2e spawn: all expected business groups produce discovery/config ack.

Acceptance criteria:
- root consistently receives DiscoveryAck/ConfigAck from all non-exempt business groups;
- `start_work_on_all_groups_ready=true` does not block forever.

---

## Phase 2 — Deterministic Shutdown Semantics (Control-Plane Driven)

Purpose:
- make shutdown completion independent from fragile tombstone assumptions in deep ring chains.

Actions:
1. Define shutdown protocol in code/docs as explicit sequence:
- business groups reach drain-ready,
- root issues per-worker stop commands on control lane,
- workers reply stop-ack,
- root finalizes when expected stop-acks reached.
2. Keep tombstone as end-of-data indicator, not sole global shutdown oracle.
3. Ensure one canonical drain-ready emission path per worker group (no duplicate semantic channels).
4. Add guard diagnostics for drop points:
- drain-ready produced but not dispatched,
- dispatched but not consumed,
- consumed but not counted.

Acceptance criteria:
- no run ends with timeout-based process kill when data processing completed;
- root shutdown state transitions are monotonic and visible in logs.

---

## Phase 3 — Throughput Recovery and Hot-Path Hygiene

Purpose:
- recover processing speed without reintroducing lifecycle races.

Actions:
1. Profile before optimization (`py-spy` or `cProfile`) on same workload.
2. Separate startup-delay effects from per-message processing overhead:
- measure time to first processed business message,
- measure steady-state records/sec.
3. Audit hot path changes added after baseline:
- runner per-message extra function calls,
- additional scheduler tick routing fanout,
- excessive log/debug emission in tight loops.
4. Optimize only profile-proven hotspots.

Acceptance criteria:
- throughput returns near baseline (target threshold agreed by measured historical runs);
- no regression in handshake/shutdown correctness.

---

## Phase 4 — Scale Validation (5 -> 10 Processes)

Purpose:
- ensure fixes are not only valid in low-scale topology.

Actions:
1. Run abstract platform e2e with 5-process ring + observability.
2. Run abstract platform e2e with 10-process ring + observability and higher volume.
3. Run business experiment scenario with same metrics capture.
4. Verify tombstone propagation and stop chain at scale.

Acceptance criteria:
- 10-process test does not stall on tombstone_seen/drain-ready chain;
- observability path does not block business completion.

---

## Test Plan (Mandatory Gates for This Recovery)

1. Unit tests:
- CP routing map completeness for lifecycle events.
- readiness service transitions and dedupe behavior.

2. Integration/e2e tests:
- real spawn CP handshake completeness per group;
- ring data-plane completion + coordinated shutdown;
- observability worker coexistence without shutdown deadlock.

3. Smoke profile check:
- one run with profiler attached and archived flame/profile artifact.

---

## Implementation Order (Practical)

1. Phase 0 instrumentation baseline.
2. Phase 1 handshake fix (H-1).
3. Phase 2 shutdown protocol hardening.
4. Phase 3 performance profiling + targeted optimization.
5. Phase 4 scale validation and final sign-off.

Rationale:
- without handshake correctness, performance numbers are misleading;
- without deterministic stop chain, throughput improvements are operationally useless.

---

## Risks and Mitigations

Risk 1:
- Fixing throughput first may hide handshake deadlock.
Mitigation:
- block performance tuning until H-1 is green.

Risk 2:
- New shutdown logic duplicates old path and creates competing signals.
Mitigation:
- enforce single canonical drain-ready pipeline and remove dead paths after migration tests.

Risk 3:
- Added diagnostics itself affects timing.
Mitigation:
- keep diagnostics gated and lightweight; run control A/B with diagnostics off.

---

## Definition of Done

All conditions must hold:

1. Business run processes full dataset deterministically.
2. Root receives required CP acks from all expected groups.
3. Coordinated shutdown completes without timeout kill.
4. 5-process and 10-process e2e ring tests pass.
5. Throughput is restored to acceptable baseline band.
6. Recovery findings documented back into `_work/problems_to_solve` with exact commit references.
