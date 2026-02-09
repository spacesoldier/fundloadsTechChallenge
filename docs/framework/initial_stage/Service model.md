# Service model (`@service`)

This document formalizes what a **service** is in this framework,
why it exists, and how it should be used with ports/adapters.

It complements:

- [Ports and adapters model](Ports%20and%20adapters%20model.md)
- [Injection and strict mode](Injection%20and%20strict%20mode.md)
- [Application context and discovery](Application%20context%20and%20discovery.md)

---

## 1) Problem this solves

Standard adapters are transport-oriented and intentionally small
(`stream`, `kv_stream`, `kv`, `request`, `response`).

Sometimes one node needs a richer API over one data scope:

- multiple read/write operations
- composed operations with invariants
- convenience methods hiding low-level key/value details

Creating many ad-hoc domain ports for this leads to duplicated abstractions.
A **service** is the right middle layer: domain API on top of standard ports.

---

## 2) Definition

A service is an injectable component that:

- exposes domain-level methods
- internally uses standard framework ports/adapters
- is instantiated by framework lifecycle rules (scenario-scoped)

Service is not a new transport primitive.  
It is an orchestration layer above existing port primitives.

Rule of thumb:

- if required behavior fits existing stable ports (`stream`, `kv_stream`, `kv`, `request`, `response`),
  do not introduce a new port;
- if you need a richer multi-method API over one data scope, introduce a **service**.

---

## 3) Declaration and injection contract

### 3.1 Node-side usage (already available)

Use:

- `inject.service(MyServiceImpl)`

Current injection helper supports this directly.

### 3.2 Discovery marker (`@service`)

Planned explicit marker:

- `@service(name="window_store_service")`

Rationale:

- separate semantic intent from regular `@node`
- simplify discovery diagnostics and editor linting
- enforce service-specific lifecycle checks

Until the dedicated decorator is introduced, services can be represented as
regular framework-managed components and injected with `inject.service(...)`.

---

## 4) Lifecycle and scope

Default service lifecycle:

- one service instance per scenario/vertical

This matches existing injection scope behavior and avoids cross-scenario state bleed.

Implications:

- in-memory caches inside service are scenario-local
- no global singleton service by default

---

## 5) Example: Window store service

Typical shape:

1. Service injects `kv` adapter
2. Service methods implement domain operations (read snapshot, apply update, etc.)
3. Node injects the service, not raw low-level store adapter

This keeps node code business-focused and transport-agnostic.

When backend changes (`dict` -> Redis), service API remains stable.

---

## 6) Python practice for in-memory cache/storage

For local in-process cache/state, common Python baseline is:

- `dict` wrapper with explicit API

Framework/project pattern:

- adapter: thin `dict` transport implementation
- service: domain semantics and invariants
- node: business decision logic only

Optional production evolutions:

- Redis
- Memcached
- SQLite/postgres-backed key-value facade

Service contract should stay unchanged across these backend swaps.

---

## 7) Design rules

1. Do not expose backend details from service API.
2. Service methods should operate on domain models, not transport tuples.
3. Service should use only stable framework ports internally.
4. Node should not know if service uses `dict`, Redis, or anything else.
5. Scenario scope is default; wider scope must be explicit and justified.
6. A new custom port is allowed only for a new transport primitive, not for domain convenience APIs.

---

## 8) Test cases (TDD checklist)

1. `inject.service(ServiceImpl)` resolves correctly in scenario scope.
2. Two scenarios get different service instances.
3. Service wraps `kv` adapter operations without leaking transport details.
4. Replacing adapter factory (`dict` -> fake redis adapter) does not require node changes.
5. Missing service binding:
   - strict mode -> fail fast
   - non-strict mode (if enabled for this path) -> explicit warning path.
6. Service state does not leak between scenarios.
7. Service discovery diagnostics identify service components distinctly from nodes.
