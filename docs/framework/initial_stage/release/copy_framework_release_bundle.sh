#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  copy_framework_release_bundle.sh <destination_dir> [--framework-name <name>] [--clean] [--dry-run]

Description:
  Creates a release-ready framework bundle in <destination_dir> by copying
  stream_kernel code and framework documentation from this repository.

Options:
  --framework-name  Override framework package name in bundled pyproject.toml.
                    Default: ringo
  --clean           Remove destination_dir before copying.
  --dry-run         Print planned actions without writing files.
USAGE
}

DEST_DIR=""
CLEAN=0
DRY_RUN=0
FRAMEWORK_PACKAGE_NAME="ringo"
FRAMEWORK_SRC_PACKAGE="stream_kernel"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --framework-name)
      if [[ $# -lt 2 ]]; then
        echo "Option --framework-name requires a value." >&2
        usage >&2
        exit 1
      fi
      FRAMEWORK_PACKAGE_NAME="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$DEST_DIR" ]]; then
        echo "Destination is already specified: $DEST_DIR" >&2
        usage >&2
        exit 1
      fi
      DEST_DIR="$1"
      shift
      ;;
  esac
done

if [[ -z "$DEST_DIR" ]]; then
  echo "Destination directory is required." >&2
  usage >&2
  exit 1
fi

if [[ -z "${FRAMEWORK_PACKAGE_NAME// }" ]]; then
  echo "Framework package name must be non-empty." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
AGENT_PACKAGE_VSCODE_DIR="${SCRIPT_DIR}/agent_package/vscode"
DEST_DIR_ABS="$(mkdir -p -- "$(dirname -- "$DEST_DIR")" && cd -- "$(dirname -- "$DEST_DIR")" && pwd)/$(basename -- "$DEST_DIR")"

COPY_FILES=(
  "README.md"
  "LICENSE"
  "AGENTS.md"
  "pyproject.toml"
  "poetry.lock"
)

COPY_DIRS=(
  ".agents/skills"
  "tools/framework_kb_mcp"
  "src/stream_kernel"
  "tests/architecture"
  "tests/smoke"
  "tests/stream_kernel"
  "docs/framework"
  "docs/implementation"
  "docs/guide"
  "docs/diagrams"
)

OPTIONAL_COPY_FILES=(
  "settings.json"
  "tasks.json"
  "extensions.json"
  "ringo.code-snippets"
  "launch.json"
)

EXCLUDES=(
  "--exclude=__pycache__/"
  "--exclude=.pytest_cache/"
  "--exclude=.mypy_cache/"
  "--exclude=.ruff_cache/"
  "--exclude=*.pyc"
  "--exclude=*.pyo"
  "--exclude=.DS_Store"
)

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

rewrite_pyproject_for_framework_release() {
  local pyproject_path="$1"
  local framework_name="$2"
  local src_package="$3"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] rewrite pyproject.toml for framework release:"
    echo "[dry-run]   tool.poetry.name = ${framework_name}"
    echo "[dry-run]   packages = [{ include = \"${src_package}\", from = \"src\" }]"
    return 0
  fi
  if [[ ! -f "$pyproject_path" ]]; then
    echo "pyproject.toml not found for rewrite: $pyproject_path" >&2
    exit 1
  fi
  python3 - "$pyproject_path" "$framework_name" "$src_package" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
framework_name = sys.argv[2]
src_package = sys.argv[3]
text = path.read_text(encoding="utf-8")
original = text

name_replaced = False
packages_replaced = False

def _replace_once(pattern: str, replacement: str, source: str) -> tuple[str, bool]:
    if re.search(pattern, source, flags=re.MULTILINE):
        return re.sub(pattern, replacement, source, count=1, flags=re.MULTILINE), True
    return source, False

text, name_replaced = _replace_once(
    r'^name\s*=\s*".*?"\s*$',
    f'name = "{framework_name}"',
    text,
)
text, packages_replaced = _replace_once(
    r'^packages\s*=\s*\[.*\]\s*$',
    f'packages = [{{ include = "{src_package}", from = "src" }}]',
    text,
)

if not name_replaced:
    raise SystemExit("Cannot rewrite pyproject.toml: [tool.poetry] name field not found")
if not packages_replaced:
    raise SystemExit("Cannot rewrite pyproject.toml: [tool.poetry] packages field not found")

if text != original:
    path.write_text(text, encoding="utf-8")
PY
}

if [[ -d "$DEST_DIR_ABS" && "$CLEAN" -eq 1 ]]; then
  run_cmd rm -rf -- "$DEST_DIR_ABS"
fi

if [[ -d "$DEST_DIR_ABS" && "$CLEAN" -ne 1 ]]; then
  if [[ -n "$(find "$DEST_DIR_ABS" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; then
    echo "Destination is not empty: $DEST_DIR_ABS" >&2
    echo "Use --clean if you want to overwrite it." >&2
    exit 1
  fi
fi

run_cmd mkdir -p -- "$DEST_DIR_ABS"

for item in "${COPY_FILES[@]}"; do
  src_path="${REPO_ROOT}/${item}"
  dst_path="${DEST_DIR_ABS}/${item}"
  if [[ ! -f "$src_path" ]]; then
    echo "Required file not found: $src_path" >&2
    exit 1
  fi
  run_cmd mkdir -p -- "$(dirname -- "$dst_path")"
  run_cmd cp -a -- "$src_path" "$dst_path"
done

for item in "${OPTIONAL_COPY_FILES[@]}"; do
  src_path="${AGENT_PACKAGE_VSCODE_DIR}/${item}"
  dst_path="${DEST_DIR_ABS}/.vscode/${item}"
  if [[ ! -f "$src_path" ]]; then
    continue
  fi
  run_cmd mkdir -p -- "$(dirname -- "$dst_path")"
  run_cmd cp -a -- "$src_path" "$dst_path"
done

for item in "${COPY_DIRS[@]}"; do
  src_path="${REPO_ROOT}/${item}"
  dst_path="${DEST_DIR_ABS}/${item}"
  if [[ ! -d "$src_path" ]]; then
    echo "Required directory not found: $src_path" >&2
    exit 1
  fi
  run_cmd mkdir -p -- "$dst_path"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] rsync -a %s %q/ %q/\n' "${EXCLUDES[*]}" "$src_path" "$dst_path"
  else
    rsync -a "${EXCLUDES[@]}" -- "$src_path/" "$dst_path/"
  fi
done

rewrite_pyproject_for_framework_release \
  "${DEST_DIR_ABS}/pyproject.toml" \
  "${FRAMEWORK_PACKAGE_NAME}" \
  "${FRAMEWORK_SRC_PACKAGE}"

BUNDLE_INFO_PATH="${DEST_DIR_ABS}/framework_release_bundle_info.txt"
MANIFEST_PATH="${DEST_DIR_ABS}/framework_release_manifest.txt"
SOURCE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")"

if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<EOF
[dry-run] would write:
  - ${BUNDLE_INFO_PATH}
  - ${MANIFEST_PATH}
EOF
else
  {
    echo "bundle_created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "bundle_source_repo=${REPO_ROOT}"
    echo "bundle_source_commit=${SOURCE_COMMIT}"
    echo "bundle_target_dir=${DEST_DIR_ABS}"
    echo "framework_package_name=${FRAMEWORK_PACKAGE_NAME}"
    echo "framework_src_package=${FRAMEWORK_SRC_PACKAGE}"
    echo "pyproject_rewrite=applied"
  } > "$BUNDLE_INFO_PATH"

  (
    cd "$DEST_DIR_ABS"
    find . -type f \
      ! -name "framework_release_manifest.txt" \
      -print \
      | sed 's|^\./||' \
      | sort
  ) > "$MANIFEST_PATH"
fi

echo "Framework release bundle is ready: ${DEST_DIR_ABS}"
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "Manifest: ${MANIFEST_PATH}"
  echo "Bundle info: ${BUNDLE_INFO_PATH}"
fi
