#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="src:${PYTHONPATH}"
else
  export PYTHONPATH="src"
fi

PYTHON_BIN=""
if [[ -n "${RESEARCH_UI_MCP_PYTHON:-}" ]]; then
  if [[ -x "${RESEARCH_UI_MCP_PYTHON}" ]]; then
    PYTHON_BIN="${RESEARCH_UI_MCP_PYTHON}"
  else
    echo "RESEARCH_UI_MCP_PYTHON is set but not executable: ${RESEARCH_UI_MCP_PYTHON}" >&2
    exit 2
  fi
else
  for candidate in \
    "$ROOT_DIR/research_ui/.venv/bin/python" \
    "$ROOT_DIR/.venv/bin/python"
  do
    if [[ -x "$candidate" ]]; then
      if "$candidate" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('mcp') else 1)" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
      fi
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "No Python interpreter with 'mcp' module was found." >&2
  echo "Checked:" >&2
  echo "  - $ROOT_DIR/research_ui/.venv/bin/python" >&2
  echo "  - $ROOT_DIR/.venv/bin/python" >&2
  echo "Install deps in research_ui env, e.g.: (cd research_ui && poetry install)" >&2
  exit 2
fi

exec "$PYTHON_BIN" -m research_ui.mcp.postgres_server
