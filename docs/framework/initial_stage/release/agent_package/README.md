# Ringo Platform Kit: Agent Package and Runtime Guide

`stream_kernel` (working release name: `ringo`) is a framework and execution environment for enterprise-grade pipelines where all of the following are required at once:

- non-trivial domain logic and multi-stage processing
- deterministic behavior and reproducible outcomes
- explicit observability (logs, traces, metrics)
- high throughput and controlled scalability across processes

This folder is a release-oriented package for agents and teams that build applications on top of the framework, not for re-inventing the framework itself.

## What This Package Gives You

- a strict agent constitution (`AGENTS`, `CLAUDE`, Cursor rules)
- executable quality gates (local + CI)
- practical examples (golden patterns and anti-patterns)
- MCP knowledge contract for searchable framework docs
- IDE hooks so checks are not just "recommendations"

## Core System Concepts

Use these terms consistently in code, docs, and review comments.

- `Envelope`: transport/runtime container carrying payload plus execution metadata.
- `@node`: pure message handler boundary in graph terms; receives input, emits outputs.
- `@service`: imperative helper with domain/platform operations used by nodes.
- `Store` (no decorator): state boundary (in-memory or externalized) injected into services.
- `@adapter`: integration boundary for external systems (IO, DB, APIs, telemetry sinks).
- `Port`: stable interface injected into nodes/services; adapters implement ports.
- `Scenario Step`: routing/execution step declaring consumers and output flow.
- `Process Group`: set of nodes assigned to one runtime process role.
- `Root`: control-plane coordinator for startup/lifecycle/shutdown orchestration.
- `Leaf`: worker process executing assigned graph segment.
- `Data plane`: business payload flow between leaf processes.
- `Control plane`: lifecycle/config/discovery/commands between root and leafs.
- `Observability plane`: trace/log/metric flow to observability worker(s).
- `Tombstone`: end-of-stream semantic marker used in completion/shutdown chains.
- `Quorum`: completion condition across required nodes/workers before next lifecycle phase.

## Execution Model (High-Level)

1. Root brings up minimal system rails.
2. Discovery/config binds runtime graph and process groups.
3. Root starts leaf workers and establishes transport lanes.
4. Business data flows leaf-to-leaf (ring/data-plane), not through root data fan-in by default.
5. Observability events flow to observability process(es).
6. Shutdown is driven by explicit lifecycle signals and completion criteria, not implicit timeout guesses.

## Configuration Model (How Apps Shape Runtime)

Application teams define:

- which nodes exist
- how they route
- which process group owns each node
- runtime settings for transport/observability/lifecycle budgets

Minimal sketch (illustrative):

```yaml
runtime:
  process_groups:
    - name: execution.ingress
      workers: 1
      nodes: [source:source, parse_record, enrich_record]
    - name: execution.transform
      workers: 2
      nodes: [compute_features, apply_policy, normalize_output]
    - name: execution.egress
      workers: 1
      nodes: [format_output, sink:sink]
    - name: system.observability
      workers: 1
      nodes:
        - system.obs.trace_dispatch
        - system.obs.log_dispatch
        - system.obs.monitor_dispatch
```

This package includes concrete examples for node config and process-group splits in:

- `examples/golden/node_config_and_process_groups.md`
- `examples/golden/ring_pipeline.md`

## Adapter Model: Async by Default

- adapters should be treated as non-blocking boundaries by default
- blocking IO must never stall runner/event-loop execution

Recommended execution policy:

1. `async_native`: adapter uses native async client/library.
2. `thread_wrapped`: blocking client wrapped via `asyncio.to_thread` or dedicated thread worker.
3. `process_wrapped`: heavy CPU/blocking tasks moved to process boundary when needed.

Practical rule:

- Nodes/services depend on `Port` interfaces only.
- Port implementation can be swapped (file, DB, API, mock, Redis, etc.) with no business-logic rewrite.
- Any adapter path that can block must be isolated from the main message loop.

This keeps business determinism while allowing IO concurrency and throughput growth.

## Contents of This Folder

- `AGENTS.ringo.template.md`: mandatory repo-level constitution for agents.
- `CLAUDE.ringo.template.md`: equivalent constitution template for Claude-style agents.
- `cursor_rules/`: Cursor `.mdc` rule templates.
- `golden_examples_plan.md`: list of canonical examples and anti-patterns.
- `examples/`: practical app-building examples for developers and agents.
- `skills_blueprint.md`: blueprint for repeatable agent skills.
- `skills/`: copy-ready `SKILL.md` templates for app-level workflows.
- `mcp_knowledge_contract.md`: read-only MCP contract (`search`, `fetch`) for docs retrieval.
- `enforcement_gates.md`: mandatory local/CI gates.
- `ide_tooling.md`: VS Code tasks/settings/snippets usage.
- `vscode/`: curated VS Code templates.
- `ci/github_agent_gates_workflow.yml`: CI template for gates.
- `install_agent_tooling_bundle.sh`: self-contained installer for these artifacts.

## Quick Start for a New App Repository

1. Install tooling bundle:

```bash
docs/framework/initial_stage/release/agent_package/install_agent_tooling_bundle.sh /path/to/target/repo --dry-run
docs/framework/initial_stage/release/agent_package/install_agent_tooling_bundle.sh /path/to/target/repo --force
```

Installer also deploys ready skill templates into `.agents/skills`.

2. Copy and tune constitutions:
- `AGENTS.ringo.template.md` -> `AGENTS.md`
- `CLAUDE.ringo.template.md` -> `CLAUDE.md`

3. Use examples from `examples/golden` to scaffold first module.
4. Enable gates from `enforcement_gates.md` in CI and local IDE tasks.
5. Connect MCP doc tools for agent context lookup:

```bash
.venv/bin/python -m tools.framework_kb_mcp mcp-serve
```

6. Validate module invariants quickly:

```bash
.venv/bin/python -m stream_kernel.doctor check-module src/stream_kernel/adapters/file_io.py --json
```

## Non-Goals

- This package does not replace runtime docs in `docs/framework/**`.
- This package does not authorize off-graph bypasses for lifecycle/data routing.
- This package does not relax deterministic-output guarantees for convenience.

## Design Intent

- Rules are strict and short.
- Deep knowledge is searchable via MCP instead of duplicated prose.
- Examples are application-centric and copy/paste friendly.
- Architecture compliance is enforced by tests/gates, not optional conventions.
