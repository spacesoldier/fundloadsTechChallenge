#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="src:${PYTHONPATH}"
else
  export PYTHONPATH="src"
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv/bin/python. Run poetry install first." >&2
  exit 2
fi

exec .venv/bin/python -m research_ui.mcp.postgres_server
