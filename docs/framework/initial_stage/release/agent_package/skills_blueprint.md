# Skills Blueprint for Ringo Agents

This package now targets **application development on top of the framework**, not framework-internals-only operations.

## Why This Changed

Previous draft skills were too control-plane specific (`wire-control-plane-node`) and partly mismatched runtime reality (`add-routing-rule` wording).  
The revised set focuses on what app teams repeatedly need:

- turn requirements into runtime design
- implement node/service/store/adapter modules
- define config + process-group split
- add deterministic tests and gates

## Recommended Skills Set

```text
.agents/skills/
  feature-to-pr/
    SKILL.md
  decompose-feature-to-runtime-design/
    SKILL.md
  scaffold-node-service-store/
    SKILL.md
  scaffold-port-and-adapter/
    SKILL.md
  map-config-and-process-groups/
    SKILL.md
  build-e2e-pipeline-test/
    SKILL.md
```

## Ready Templates In This Package

Prebuilt templates are included here and can be copied into a target repository:

```text
docs/framework/initial_stage/release/agent_package/skills/
```

Copy command:

```bash
mkdir -p .agents/skills
cp -R docs/framework/initial_stage/release/agent_package/skills/* .agents/skills/
```

## Skill Contracts (Short)

### 1) feature-to-pr

Purpose:
- run full delivery loop for one feature:
  - requirement decomposition
  - runtime module implementation
  - deterministic tests
  - architecture/smoke gates
  - PR-ready summary

Output:
- end-to-end patchset
- test/gate run report
- residual risks list

### 2) decompose-feature-to-runtime-design

Purpose:
- convert product requirement to explicit runtime design:
  - messages
  - nodes
  - services
  - stores
  - adapters/ports
  - config knobs
  - deterministic test plan

Output:
- design note
- implementation checklist
- test matrix

### 3) scaffold-node-service-store

Purpose:
- generate a clean module slice with node + service + store + tests.

Output:
- runtime code
- unit tests
- docs update

### 4) scaffold-port-and-adapter

Purpose:
- add integration boundary through a stable port interface and adapter implementation.

Output:
- port contract
- adapter implementation
- DI binding
- validation/runtime tests

### 5) map-config-and-process-groups

Purpose:
- map node set to process groups and runtime config for scalable deterministic execution.

Output:
- config patch
- process-group mapping rationale
- startup/shutdown impact note

### 6) build-e2e-pipeline-test

Purpose:
- create deterministic e2e test for multi-step or multi-process pipeline behavior.

Output:
- e2e test fixture
- expected outputs
- assertions for completion and observability basics
