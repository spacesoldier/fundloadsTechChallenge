# Agent Examples Pack (Application-First)

These examples are for writing an **application on top of** `stream_kernel`, not for re-implementing the framework.

Assumption:
- your app already depends on the packaged framework in Poetry (for example package name `ringo`, import path `stream_kernel`)

Use this folder as a cookbook:
- `golden/*` shows recommended app-level patterns
- `anti_patterns/*` shows common mistakes in app code

Start with:
- `golden/minimal_runtime_component.md` for `@node` + `@service` + `inject.*`
- `golden/minimal_adapter.md` for `@adapter` and port binding
- `golden/platform_ports_and_injection.md` for platform ports overview and config-in-node usage
- `golden/file_source_adapter.md` for project-style file ingress via `source:source`
- `golden/file_egress_adapter.md` for project-style file egress via `sink:sink`
- `golden/node_config_and_process_groups.md` for node YAML config and multi-process group layout

If an agent proposes touching framework internals for normal app tasks, treat it as a smell first.
