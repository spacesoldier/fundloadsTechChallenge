# Ports module retirement plan (`fund_load/ports`)

## Goal
Retire `src/fund_load/ports` and move to the framework-owned port model
(`stream`, `kv_stream`, `kv`, `request`, `response`) without breaking the demo project.

This is a staged migration plan, not a one-shot refactor.

---

## Why this is needed

- The framework already defines the communication taxonomy by `port_type`.
- Project-level `ports` create a second contract layer and hide framework drift.
- Historically, `stream_kernel/app/runtime.py` depended on
  `fund_load.ports.trace_sink.TraceSink`; this dependency is now removed.

---

## Current status snapshot

1. `src/fund_load/ports` has been removed from code.
2. Usecases now depend on service contracts (`PrimeCheckerService`, `WindowStoreService`) or framework stream bindings.
3. IO adapters are framework-driven (`stream_kernel.adapters.file_io`) with project mapping in `fund_load.adapters.io`.
4. Remaining work is mostly documentation/test-structure cleanup, not runtime behavior migration.

2. Not all current interfaces are true framework ports:
   - `InputSource` / `OutputSink` / `TraceSink` are infrastructure-like and should be framework-level.
   - `PrimeChecker` and `WindowReadPort`/`WindowWritePort` are domain-specific service contracts.

3. Stable framework ports are transport-shaped (`stream`, `kv`, `request`, ...),
   while project contracts expose domain methods (`read_snapshot`, `is_prime`, etc.).

4. Project needs richer APIs in some places (WindowStore-like behavior), but this
   should be solved with framework-managed **services**, not extra domain ports.

---

## Staged execution plan

### Stage A: Decouple framework from project ports (safe first)

- [x] Introduce framework trace sink contract under `stream_kernel` and stop importing
      `fund_load.ports.trace_sink` from framework runtime.
- [x] Keep behavior unchanged, only remove cross-package dependency.
- [x] Add regression tests to ensure runtime tracing path still works.

### Stage B: Retire infrastructure-like project ports

- [x] Remove project-level `InputSource` and `OutputSink` usage from steps/adapters/tests.
- [x] Use framework-owned contracts and adapter metadata (`@adapter`, `binds`) for IO boundaries.
- [x] Remove legacy `tests/ports/*` suite after equivalent framework/adapter tests cover contracts.

### Stage C: Reclassify domain-specific contracts

- [x] Introduce service layer for domain APIs (`@service` / `inject.service(...)` model).
- [x] Move `PrimeChecker` and `Window*` behavior behind service contracts.
- [x] Back services with framework port adapters (`kv`, `request`, etc.), not project ports.
- [x] Keep step code readable; avoid leaking low-level transport calls into business logic.
- [x] Add tests proving strict equivalence of behavior before/after relocation.

### Stage D: Full removal

- [x] Delete `src/fund_load/ports`.
- [x] Replace imports in app/integration/usecase tests.
- [ ] Remove redundant compatibility aliases.
- [ ] Update framework and project docs to reflect the final contract map.

---

## Design constraints during migration

1. No runtime behavior drift for baseline/experimental scenarios.
2. TDD only: tests first for each stage.
3. Keep deterministic ordering guarantees.
4. Avoid introducing project-specific logic into `stream_kernel`.

---

## Risks and mitigations

1. **Risk:** accidental semantic rewrite while changing interfaces.
   **Mitigation:** behavior-lock integration tests (`baseline` and `exp`) before each stage.

2. **Risk:** overfitting domain services into generic transport ports too early.
   **Mitigation:** keep an intermediate domain-contract layer in project until runner/router
   integration is fully stable.

3. **Risk:** test churn across many files.
   **Mitigation:** migrate in slices (A -> B -> C -> D), commit each stage separately.

---

## Suggested first executable task (next)

Stage A:

- add framework-native trace sink protocol,
- switch runtime to it,
- keep project trace sink adapter implementation unchanged,
- run full test suite.

---

## Service-centric migration notes

1. Service scope is scenario-local by default.
2. Service may aggregate multiple adapter operations under one domain method API.
3. Swapping backend (`dict` -> Redis) should require only adapter replacement.
4. Nodes depend on service API only (`inject.service(...)`), not on transport APIs.
