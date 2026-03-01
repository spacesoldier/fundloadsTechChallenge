# Scenario contract exchange (client-facing workflows)

This document introduces the **scenario contract** concept: a shareable,
client-facing representation of how a service is used, derived from
`workflow` + `@node` graphs.

The goal is to publish **client logic only** (what a consumer must do and
what they can expect), while keeping **internal service logic private**.
This is not a replacement for OpenAPI or GraphQL. It is a complementary
contract that captures **usage scenarios** and **behavioral expectations**
that ordinary API specs do not express.

---

## 1) Scope and intent

A scenario contract describes:

- **Flow graph**: the ordered / conditional sequence of client-visible
  steps (nodes) in a scenario.
- **Node contracts**: inputs, outputs, error modes, side-effects, and
  idempotency expectations relevant to the client.
- **Scenario guarantees**: ordering, determinism/consistency, timeouts,
  retry semantics, and correlation rules visible to the client.
- **Compatibility surface**: versioning rules and backward-compatibility
  expectations for clients.

It **does not** reveal:

- internal orchestration, private nodes, or infrastructure topology
- internal data stores, queues, or service boundaries
- internal observability or security plumbing

---

## 2) Why this exists

OpenAPI/GraphQL primarily document **shape** (types, endpoints), not
**behavior** (usage scenarios). That leaves clients to read prose and
reverse-engineer workflows, which causes long validation and alignment
cycles.

Scenario contracts provide a **formal, machine-readable** description of
how to use the service correctly, enabling:

- generated client workflows (not just request stubs)
- deterministic integration tests and replay
- contract-level negotiation across stacks

---

## 3) Relationship to the kernel

- `@node` definitions already encode the smallest meaningful behavioral
  unit (action).
- `workflow` / scenario assembly already forms a graph of those actions.

The scenario contract is a **projection** of this graph into a
client-visible form:

- keep public nodes
- redact internal nodes
- attach only client-relevant metadata

---

## 4) Roadmap tie-in (web adapters)

As web adapters mature (FastAPI, later Django and GraphQL), they should
expose scenario contracts as a **parallel artifact** alongside standard
API schemas.

This enables:

- adapter-specific runtime + shared scenario semantics
- portable client logic across stacks
- future cross-language runtime alignment

---

## 5) Next steps (placeholder)

- Define a minimal `ScenarioContract v0.1` schema.
- Specify how to derive it from `workflow` graphs.
- Introduce a client-side complementary graph generator.

---

See also: [Scenario vs node axes](./Scenario%20vs%20node%20axes.md),
[Node and stage specifications](./Node%20and%20stage%20specifications.md),
[Execution planning model](./Execution%20planning%20model.md)
