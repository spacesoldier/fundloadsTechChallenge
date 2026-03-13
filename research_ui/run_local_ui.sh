#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"

export PYTHONPATH="${ROOT_DIR}/src:${ROOT_DIR}"
export RESEARCH_UI_REPORTS_DIR="${RESEARCH_UI_REPORTS_DIR:-${ROOT_DIR}/research_ui/reports}"
export RESEARCH_UI_REDIS_HOST="${RESEARCH_UI_REDIS_HOST:-127.0.0.1}"
export RESEARCH_UI_REDIS_PORT="${RESEARCH_UI_REDIS_PORT:-6379}"
export RESEARCH_UI_REDIS_DB="${RESEARCH_UI_REDIS_DB:-0}"
export RESEARCH_UI_REDIS_KEY_PREFIX="${RESEARCH_UI_REDIS_KEY_PREFIX:-stream_kernel:debug}"
export RESEARCH_UI_POSTGRES_DSN="${RESEARCH_UI_POSTGRES_DSN:-postgresql://postgres:postgres@127.0.0.1:5432/research_ui}"
export RESEARCH_UI_MONGO_URI="${RESEARCH_UI_MONGO_URI:-mongodb://127.0.0.1:27017}"
export RESEARCH_UI_MONGO_DB="${RESEARCH_UI_MONGO_DB:-research_ui}"
export RESEARCH_UI_LOG_LEVEL="${RESEARCH_UI_LOG_LEVEL:-INFO}"

poetry run uvicorn research_ui.webapp:app --host 0.0.0.0 --port 8091 --reload
