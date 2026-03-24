#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_agent_tooling_bundle.sh <target_repo_dir> [--dry-run] [--force]

Description:
  Installs agent tooling artifacts from this release package into target repository.
  Script is self-contained: it only copies files located next to this script.

Options:
  --dry-run   Print actions without writing files.
  --force     Overwrite existing destination files.
USAGE
}

TARGET_DIR=""
DRY_RUN=0
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$TARGET_DIR" ]]; then
        echo "Target already specified: $TARGET_DIR" >&2
        exit 1
      fi
      TARGET_DIR="$1"
      shift
      ;;
  esac
done

if [[ -z "$TARGET_DIR" ]]; then
  usage >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR_ABS="$(mkdir -p -- "$TARGET_DIR" && cd -- "$TARGET_DIR" && pwd)"

copy_file() {
  local src="$1"
  local dst="$2"
  if [[ ! -f "$src" ]]; then
    echo "Missing source file: $src" >&2
    exit 1
  fi
  if [[ -f "$dst" && "$FORCE" -ne 1 ]]; then
    echo "Skip existing (use --force): $dst"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] copy $src -> $dst"
    return 0
  fi
  mkdir -p -- "$(dirname -- "$dst")"
  cp -a -- "$src" "$dst"
}

copy_dir_flat() {
  local src_dir="$1"
  local dst_dir="$2"
  if [[ ! -d "$src_dir" ]]; then
    echo "Missing source dir: $src_dir" >&2
    exit 1
  fi
  while IFS= read -r -d '' file; do
    local base
    base="$(basename -- "$file")"
    if [[ "$base" == _Index_of_* ]]; then
      continue
    fi
    local rel="${file#${src_dir}/}"
    copy_file "$file" "${dst_dir}/${rel}"
  done < <(find "$src_dir" -type f -print0)
}

# Repo-level constitution files
copy_file "${SCRIPT_DIR}/AGENTS.ringo.template.md" "${TARGET_DIR_ABS}/AGENTS.md"
copy_file "${SCRIPT_DIR}/CLAUDE.ringo.template.md" "${TARGET_DIR_ABS}/CLAUDE.md"

# Cursor rules
copy_dir_flat "${SCRIPT_DIR}/cursor_rules" "${TARGET_DIR_ABS}/.cursor/rules"

# Skills templates
copy_dir_flat "${SCRIPT_DIR}/skills" "${TARGET_DIR_ABS}/.agents/skills"

# VS Code tooling
copy_dir_flat "${SCRIPT_DIR}/vscode" "${TARGET_DIR_ABS}/.vscode"

# CI workflow template
copy_file "${SCRIPT_DIR}/ci/github_agent_gates_workflow.yml" "${TARGET_DIR_ABS}/.github/workflows/agent-gates.yml"

echo "Agent tooling bundle install complete: ${TARGET_DIR_ABS}"
