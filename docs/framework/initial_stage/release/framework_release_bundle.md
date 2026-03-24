# Framework Release Bundle: PyPI + conda-forge Prep

## Purpose
This folder contains tooling to prepare a standalone `stream_kernel` framework bundle
from the current monorepo for publication as a library.

- Script: `copy_framework_release_bundle.sh`
- Output: a destination directory with framework code + framework docs + release manifest.

## Script

Path:
- `docs/framework/initial_stage/release/copy_framework_release_bundle.sh`

Usage:

```bash
docs/framework/initial_stage/release/copy_framework_release_bundle.sh /absolute/path/to/release_bundle --clean
```

Options:
- `--clean`: remove destination before copy.
- `--dry-run`: print actions without writing files.
- `--framework-name <name>`: override bundled package name (default `ringo`).

Example with custom framework name:

```bash
docs/framework/initial_stage/release/copy_framework_release_bundle.sh \
  /absolute/path/to/release_bundle \
  --framework-name ringo \
  --clean
```

## What Gets Copied

Mandatory artifacts:
- `README.md`
- `LICENSE`
- `AGENTS.md`
- `pyproject.toml`
- `poetry.lock`
- `.agents/skills`
- `tools/framework_kb_mcp`
- `src/stream_kernel`
- `tests/architecture`
- `tests/smoke`
- `tests/stream_kernel`
- `docs/framework`
- `docs/implementation`
- `docs/guide`
- `docs/diagrams`

Optional artifacts:
- curated IDE files (copied only if present, not whole `.vscode`):
- `.vscode/settings.json`
- `.vscode/tasks.json`
- `.vscode/extensions.json`
- `.vscode/ringo.code-snippets`
- `.vscode/launch.json`

Source for these files:
- `docs/framework/initial_stage/release/agent_package/vscode/*`
- (curated release templates, independent from repository root `.vscode`)

Generated in destination:
- `framework_release_bundle_info.txt` (timestamp, source commit, source path)
- `framework_release_manifest.txt` (full file list)

## Mandatory vs Optional Matrix

| Artifact | Required | Why |
|---|---|---|
| `src/stream_kernel` | Yes | library runtime source |
| `tests/stream_kernel` | Yes | baseline quality gates |
| `tests/architecture` | Yes | architecture invariants gates |
| `tests/smoke` | Yes | smoke/system sanity gates |
| `.agents/skills` | Yes | agent procedural workflows |
| `tools/framework_kb_mcp` | Yes | local read-only knowledge search/fetch |
| `docs/framework`, `docs/implementation`, `docs/guide`, `docs/diagrams` | Yes | canonical docs context |
| `.vscode/*` curated files | Optional | local IDE enforcement convenience |

## Why This Is Needed For PyPI / conda-forge

The current repository is a challenge monorepo and current `pyproject.toml` targets
`fund_load` package (`packages = [{ include = "fund_load", from = "src" }]`).

For publishing `stream_kernel` as a library you need release packaging metadata
that points to `src/stream_kernel`.

Script now does this automatically in the generated bundle:
- rewrites `tool.poetry.name` to `ringo` (working framework package name)
- rewrites `packages` to `[{ include = "stream_kernel", from = "src" }]`

## PyPI Checklist

1. Prepare package metadata in bundle root:
- set `tool.poetry.name` (for example `stream-kernel`)
- set release `version`
- set `description`, `authors`, `license`, `readme`
- set `packages = [{ include = "stream_kernel", from = "src" }]`

2. Validate locally:
- `poetry check`
- `poetry build`
- `python -m twine check dist/*`

3. Publish:
- TestPyPI first (`twine upload --repository testpypi dist/*`)
- Then PyPI (`twine upload dist/*`)

## conda-forge Checklist

1. Use released source (typically PyPI sdist URL) in `meta.yaml`.
2. Create/adjust recipe:
- package name/version
- source sha256
- build script (`pip install .`)
- run dependencies from package metadata
- test imports (`import stream_kernel`)

3. Open/update feedstock PR in conda-forge.

## Recommended Flow

1. Run bundle script into a clean directory.
2. Open bundle as separate release workspace.
3. Replace package metadata for `stream_kernel`.
  Note: `copy_framework_release_bundle.sh` already rewrites package metadata
  to `name = "ringo"` and `packages = [{ include = "stream_kernel", from = "src" }]`.
  Adjust only if you decide to use another final name.
4. Build + test package artifacts.
5. Publish to PyPI.
6. Update conda-forge recipe against published artifact.

## Notes

- Bundle script intentionally excludes caches and bytecode.
- If you need more docs/subtrees, extend `COPY_DIRS` in the script.
- Keep release versioning/tagging aligned with source commit from
  `framework_release_bundle_info.txt`.
