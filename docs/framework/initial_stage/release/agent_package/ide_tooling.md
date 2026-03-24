# IDE Tooling (Enforcement, Not Just Advice)

This package adds practical IDE hooks so agent guidelines are enforceable in daily work.

## VS Code files

- `.vscode/settings.json`
- `.vscode/launch.json`
- `.vscode/tasks.json`
- `.vscode/extensions.json`
- `.vscode/ringo.code-snippets`

## What each file does

1. `settings.json`
- sets default interpreter to `.venv/bin/python`
- enables pytest discovery and points to `tests/stream_kernel`
- sets `src` as analysis path for imports

2. `tasks.json`
- `Agent Gates: stream_kernel tests`
- `Agent Gates: architecture tests`
- `Agent Gates: smoke tests`
- `Agent Gates: control-plane targeted`
- `Agent Gates: runtime smoke 15s`
- `Agent Gates: full local` (composed sequence)

These tasks directly mirror `enforcement_gates.md` checks.

3. `extensions.json`
- recommends Python + Pylance + Ruff extensions for a consistent baseline

4. `ringo.code-snippets`
- `ringo-node`
- `ringo-service`
- `ringo-adapter`
- `ringo-config`

These snippets push generated code toward framework conventions.

## Why this matters

Without IDE hooks, AGENTS/examples are passive documentation.  
With tasks + snippets + test wiring, agents and developers get immediate feedback and executable gates.

## Bundle behavior

Release bundle script copies only these curated VS Code files (if present), not the whole `.vscode` folder:
- `.vscode/settings.json`
- `.vscode/launch.json`
- `.vscode/tasks.json`
- `.vscode/extensions.json`
- `.vscode/ringo.code-snippets`

## Self-contained installer

Use:

```bash
docs/framework/initial_stage/release/agent_package/install_agent_tooling_bundle.sh /path/to/repo --dry-run
docs/framework/initial_stage/release/agent_package/install_agent_tooling_bundle.sh /path/to/repo --force
```

Installer behavior:
- copies only artifacts from this `agent_package` directory
- does not scan or depend on full project tree
- installs:
  - `AGENTS.md` and `CLAUDE.md`
  - `.cursor/rules/*.mdc`
  - `.vscode/*` curated files
  - `.github/workflows/agent-gates.yml`
