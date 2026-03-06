#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8091}"
COMPOSE_FILE="${COMPOSE_FILE:-research_ui/docker-compose.yml}"

run_docker_fallback() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not available and local fastapi runtime is missing." >&2
    echo "Install dependencies with: poetry install --with research-ui" >&2
    exit 2
  fi
  echo "Local fastapi runtime is unavailable; falling back to Docker Compose." >&2
  RESEARCH_UI_HTTP_PORT="${PORT}" docker compose -f "${COMPOSE_FILE}" up --build
}

if [[ ! -x .venv/bin/python ]]; then
  run_docker_fallback
fi

if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import importlib.util
assert importlib.util.find_spec('fastapi')
assert importlib.util.find_spec('uvicorn')
PY
then
  run_docker_fallback
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="src:${PYTHONPATH}"
else
  export PYTHONPATH="src"
fi
exec .venv/bin/python -m uvicorn research_ui.webapp:app --host "$HOST" --port "$PORT" --reload
