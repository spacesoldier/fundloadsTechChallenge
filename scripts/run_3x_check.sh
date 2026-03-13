#!/usr/bin/env bash
set -euo pipefail

# Repro harness for 3 sequential fund_load runs with archival of artifacts.
# - Archives current logs/metrics/traces/outputs instead of deleting them.
# - Runs the configured scenario 3 times.
# - Enables leaf debug for one selected run (default: run 2).

CONFIG_DEFAULT="src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml"
TIMEOUT_SECONDS_DEFAULT=90
RUNS_DEFAULT=3
DEBUG_RUN_DEFAULT=2
ARCHIVE_ROOT_DEFAULT="run_archive"
PYTHON_BIN_DEFAULT=".venv/bin/python"

CONFIG_PATH="${CONFIG_DEFAULT}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS_DEFAULT}"
RUNS="${RUNS_DEFAULT}"
DEBUG_RUN="${DEBUG_RUN_DEFAULT}"
ARCHIVE_ROOT="${ARCHIVE_ROOT_DEFAULT}"
PYTHON_BIN="${PYTHON_BIN_DEFAULT}"
INTERRUPTED=0
TMP_DEBUG_CFG=""
LOCK_FD=9
LOCK_PATH="/tmp/fund_load_run_3x_check.lock"

usage() {
  cat <<'EOF'
Usage: scripts/run_3x_check.sh [options]

Options:
  --config PATH         Config path (default: src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml)
  --timeout SECONDS     Timeout per run (default: 90)
  --runs N              Number of runs (default: 3)
  --debug-run IDX       Run index to enable leaf debug logs (default: 2, use 0 to disable)
  --archive-root PATH   Archive root dir (default: run_archive)
  --python PATH         Python binary to use (default: .venv/bin/python)
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="$2"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --debug-run) DEBUG_RUN="$2"; shift 2 ;;
    --archive-root) ARCHIVE_ROOT="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  exit 2
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python binary is not executable: ${PYTHON_BIN}" >&2
  exit 2
fi

if ! command -v timeout >/dev/null 2>&1; then
  echo "'timeout' command is required" >&2
  exit 2
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "'rg' command is required" >&2
  exit 2
fi

if command -v flock >/dev/null 2>&1; then
  eval "exec ${LOCK_FD}>\"${LOCK_PATH}\""
  if ! flock -n "${LOCK_FD}"; then
    echo "Another run_3x_check.sh session is active (lock: ${LOCK_PATH})." >&2
    echo "Wait for it to finish or kill it before starting a new run." >&2
    exit 3
  fi
fi

repo_path="$(pwd)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
session_dir="${ARCHIVE_ROOT}/${timestamp}_3x"
mkdir -p "${session_dir}"

summary_file="${session_dir}/summary.txt"
touch "${summary_file}"

log() {
  echo "$*"
  echo "$*" >> "${summary_file}"
}

cleanup_python_caches() {
  local pycache_dirs=0
  local pyc_files=0
  local pyo_files=0
  local tmp_file
  tmp_file="$(mktemp)"
  find . \
    -path "./.venv" -prune -o \
    -path "./run_archive" -prune -o \
    -path "./research_ui/reports" -prune -o \
    -type d -name "__pycache__" -print > "${tmp_file}" 2>/dev/null || true
  pycache_dirs="$(wc -l < "${tmp_file}")"
  if [[ "${pycache_dirs}" -gt 0 ]]; then
    while IFS= read -r dir; do
      [[ -n "${dir}" ]] && rm -rf "${dir}" || true
    done < "${tmp_file}"
  fi
  : > "${tmp_file}"
  find . \
    -path "./.venv" -prune -o \
    -path "./run_archive" -prune -o \
    -path "./research_ui/reports" -prune -o \
    -type f -name "*.pyc" -print > "${tmp_file}" 2>/dev/null || true
  pyc_files="$(wc -l < "${tmp_file}")"
  if [[ "${pyc_files}" -gt 0 ]]; then
    while IFS= read -r file; do
      [[ -n "${file}" ]] && rm -f "${file}" || true
    done < "${tmp_file}"
  fi
  : > "${tmp_file}"
  find . \
    -path "./.venv" -prune -o \
    -path "./run_archive" -prune -o \
    -path "./research_ui/reports" -prune -o \
    -type f -name "*.pyo" -print > "${tmp_file}" 2>/dev/null || true
  pyo_files="$(wc -l < "${tmp_file}")"
  if [[ "${pyo_files}" -gt 0 ]]; then
    while IFS= read -r file; do
      [[ -n "${file}" ]] && rm -f "${file}" || true
    done < "${tmp_file}"
  fi
  rm -f "${tmp_file}" || true
  log "Python cache cleanup: __pycache__=${pycache_dirs}, pyc=${pyc_files}, pyo=${pyo_files}"
}

kill_repo_runtime_processes() {
  local pids=()
  local found=()
  mapfile -t found < <(pgrep -f "${repo_path}/\\.venv/bin/python -m fund_load" || true)
  if [[ ${#found[@]} -gt 0 ]]; then pids+=("${found[@]}"); fi
  mapfile -t found < <(pgrep -f "${repo_path}/\\.venv/bin/python -c from multiprocessing\\.spawn import spawn_main" || true)
  if [[ ${#found[@]} -gt 0 ]]; then pids+=("${found[@]}"); fi
  mapfile -t found < <(pgrep -f "${repo_path}/\\.venv/bin/python -c from multiprocessing\\.resource_tracker import main" || true)
  if [[ ${#found[@]} -gt 0 ]]; then pids+=("${found[@]}"); fi
  if [[ ${#pids[@]} -gt 0 ]]; then
    mapfile -t pids < <(printf '%s\n' "${pids[@]}" | sort -u)
  fi
  if [[ ${#pids[@]} -eq 0 ]]; then
    return 0
  fi
  log "Found runtime processes: ${pids[*]}"
  kill "${pids[@]}" 2>/dev/null || true
  sleep 1
  kill -9 "${pids[@]}" 2>/dev/null || true
}

cleanup_on_signal() {
  local sig="$1"
  INTERRUPTED=1
  echo ""
  log "Interrupted by ${sig}; stopping active runtime processes"
  kill_repo_runtime_processes
  if [[ -n "${TMP_DEBUG_CFG}" && -f "${TMP_DEBUG_CFG}" ]]; then
    rm -f "${TMP_DEBUG_CFG}" || true
  fi
  log "Interrupted; exiting with code 130"
  exit 130
}

trap 'cleanup_on_signal SIGINT' INT
trap 'cleanup_on_signal SIGTERM' TERM

archive_matches() {
  local target_dir="$1"
  shift
  mkdir -p "${target_dir}"
  local path
  for path in "$@"; do
    if [[ -e "${path}" ]]; then
      mv "${path}" "${target_dir}/"
    fi
  done
}

archive_glob() {
  local target_dir="$1"
  local glob_expr="$2"
  mkdir -p "${target_dir}"
  shopt -s nullglob
  local matches=(${glob_expr})
  shopt -u nullglob
  if [[ ${#matches[@]} -eq 0 ]]; then
    return 0
  fi
  local path
  for path in "${matches[@]}"; do
    mv "${path}" "${target_dir}/"
  done
}

archive_current_artifacts() {
  local stage="$1"
  local dst="${session_dir}/${stage}"
  mkdir -p "${dst}"
  archive_matches "${dst}/logs" \
    "logs/lifecycle_experiment_multiprocess_jaeger.log" \
    "logs/lifecycle_experiment_multiprocess_jaeger.jsonl" \
    "logs/runtime_all_experiment_multiprocess_jaeger.jsonl" \
    "logs/run_log_root.log"
  archive_matches "${dst}/outputs" \
    "output_exp_mpj.txt"
  archive_glob "${dst}/traces" "traces/trace_experiment_multiprocess_jaeger_*.jsonl"
  archive_glob "${dst}/metrics" "metrics/*"
  archive_matches "${dst}/logs" \
    "logs/leaf_debug" \
    "logs/leaf_debug_run2"
}

copy_run_artifacts() {
  local run_dir="$1"
  mkdir -p "${run_dir}"
  cp -f "logs/run_log_root.log" "${run_dir}/" 2>/dev/null || true
  cp -f "logs/lifecycle_experiment_multiprocess_jaeger.log" "${run_dir}/" 2>/dev/null || true
  cp -f "logs/lifecycle_experiment_multiprocess_jaeger.jsonl" "${run_dir}/" 2>/dev/null || true
  cp -f "logs/runtime_all_experiment_multiprocess_jaeger.jsonl" "${run_dir}/" 2>/dev/null || true
  cp -f "output_exp_mpj.txt" "${run_dir}/" 2>/dev/null || true
  cp -f traces/trace_experiment_multiprocess_jaeger_*.jsonl "${run_dir}/" 2>/dev/null || true
  cp -f metrics/* "${run_dir}/" 2>/dev/null || true
}

make_debug_cfg() {
  local src="$1"
  local dst="$2"
  cp "${src}" "${dst}"
  perl -0777 -i -pe 's/leaf_debug_enabled:\s*false/leaf_debug_enabled: true/g' "${dst}"
  perl -0777 -i -pe 's/leaf_verbose_logging:\s*false/leaf_verbose_logging: true/g' "${dst}"
  perl -0777 -i -pe 's/leaf_debug_write_to_file:\s*false/leaf_debug_write_to_file: true/g' "${dst}"
  perl -0777 -i -pe 's/runtime_debug_enabled:\s*false/runtime_debug_enabled: true/g' "${dst}"
  perl -0777 -i -pe 's/runtime_debug_direct_dispatch:\s*true/runtime_debug_direct_dispatch: false/g' "${dst}"
  perl -0777 -i -pe 's/write_mode:\s*inline/write_mode: background/g' "${dst}"
  perl -0777 -i -pe 's|leaf_debug_logs_dir:\s*logs/leaf_debug|leaf_debug_logs_dir: logs/leaf_debug_run2|g' "${dst}"
}

log "Session: ${timestamp}"
log "Config: ${CONFIG_PATH}"
log "Timeout per run: ${TIMEOUT_SECONDS}s"
log "Runs: ${RUNS}, debug run index: ${DEBUG_RUN}"
log "Archive dir: ${session_dir}"
log ""

kill_repo_runtime_processes
cleanup_python_caches
archive_current_artifacts "preexisting"

for ((i=1; i<=RUNS; i++)); do
  if [[ "${INTERRUPTED}" -ne 0 ]]; then
    break
  fi
  log "=== RUN ${i} ==="
  kill_repo_runtime_processes
  cleanup_python_caches
  archive_current_artifacts "before_run_${i}"

  run_cfg="${CONFIG_PATH}"
  if [[ "${DEBUG_RUN}" -gt 0 && "${i}" -eq "${DEBUG_RUN}" ]]; then
    run_cfg="/tmp/run_3x_check_debug_${timestamp}.yml"
    TMP_DEBUG_CFG="${run_cfg}"
    make_debug_cfg "${CONFIG_PATH}" "${run_cfg}"
    rm -rf logs/leaf_debug_run2 || true
    mkdir -p logs/leaf_debug_run2
    log "run ${i}: debug config enabled -> ${run_cfg}"
  fi

  : > logs/run_log_root.log
  tail -n +1 -f logs/run_log_root.log &
  tail_pid=$!
  set +e
  timeout --foreground --signal=TERM --kill-after=5s "${TIMEOUT_SECONDS}s" \
    env PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m fund_load --config "${run_cfg}" >> logs/run_log_root.log 2>&1
  rc=$?
  set -e
  kill "${tail_pid}" 2>/dev/null || true
  wait "${tail_pid}" 2>/dev/null || true
  kill_repo_runtime_processes

  out_lines=0
  [[ -f output_exp_mpj.txt ]] && out_lines="$(wc -l < output_exp_mpj.txt)"
  trace_all=0
  [[ -f traces/trace_experiment_multiprocess_jaeger_all.jsonl ]] && trace_all="$(wc -l < traces/trace_experiment_multiprocess_jaeger_all.jsonl)"
  trace_biz=0
  [[ -f traces/trace_experiment_multiprocess_jaeger_business_logic.jsonl ]] && trace_biz="$(wc -l < traces/trace_experiment_multiprocess_jaeger_business_logic.jsonl)"

  log "run ${i}: rc=${rc}, output=${out_lines}, trace_all=${trace_all}, trace_biz=${trace_biz}"
  rg -n "readiness timeout|readiness reached|start-work .*dispatched|boundary result failed|root loop finished|runtime shutdown started|RuntimeWorkerFailedError" logs/run_log_root.log -S | tail -n 20 | tee -a "${summary_file}" >/dev/null || true

  run_dir="${session_dir}/run_${i}"
  copy_run_artifacts "${run_dir}"
  if [[ -d logs/leaf_debug_run2 && "${DEBUG_RUN}" -gt 0 && "${i}" -eq "${DEBUG_RUN}" ]]; then
    cp -r logs/leaf_debug_run2 "${run_dir}/" || true
  fi
  if [[ -n "${TMP_DEBUG_CFG}" && -f "${TMP_DEBUG_CFG}" ]]; then
    rm -f "${TMP_DEBUG_CFG}" || true
    TMP_DEBUG_CFG=""
  fi
  log ""
done

log "=== POSTCHECK: remaining runtime processes ==="
(
  pgrep -af "${repo_path}/\\.venv/bin/python -m fund_load" || true
  pgrep -af "${repo_path}/\\.venv/bin/python -c from multiprocessing\\.spawn import spawn_main" || true
  pgrep -af "${repo_path}/\\.venv/bin/python -c from multiprocessing\\.resource_tracker import main" || true
) | tee -a "${summary_file}" >/dev/null
log ""
if [[ -f scripts/render_run_archive_report.py ]]; then
  mkdir -p research_ui/reports
  session_name="$(basename "${session_dir}")"
  report_output_path="${repo_path}/research_ui/reports/${session_name}.html"
  set +e
  report_path="$("${PYTHON_BIN}" scripts/render_run_archive_report.py --session-dir "${session_dir}" --output "${report_output_path}" 2>/dev/null)"
  report_rc=$?
  set -e
  if [[ "${report_rc}" -eq 0 && -n "${report_path}" ]]; then
    log "Report: ${report_path}"
  else
    log "Report: failed to render HTML report"
  fi
fi
log "Done. Summary: ${summary_file}"
