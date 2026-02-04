from __future__ import annotations

import json
from pathlib import Path

# Tracing runtime wiring follows docs/implementation/kernel/Composition Root Spec.md + Trace docs.
from stream_kernel.app import run


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_runtime_tracing_jsonl_sink(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  pipeline:
    - parse_load_attempt
    - compute_time_keys
    - idempotency_gate
    - compute_features
    - evaluate_policies
    - update_windows
    - format_output
    - write_output
  discovery_modules:
    - fund_load.usecases.steps
  tracing:
    enabled: true
    signature:
      mode: type_only
    context_diff:
      mode: whitelist
      whitelist:
        - line_no
    sink:
      kind: jsonl
      jsonl:
        path: trace.jsonl
        write_mode: overwrite
nodes:
  compute_time_keys:
    week_start: MON
  compute_features:
    monday_multiplier:
      enabled: false
      multiplier: 2
      apply_to: amount
    prime_gate:
      enabled: false
      global_per_day: 1
      amount_cap: 9999
  evaluate_policies:
    limits:
      daily_amount: 5000
      weekly_amount: 20000
      daily_attempts: 3
    prime_gate:
      enabled: false
      global_per_day: 1
      amount_cap: 9999
  update_windows:
    daily_prime_gate:
      enabled: false
adapters:
  input_source:
    factory: fund_load.adapters.factory:file_input_source
    settings:
      path: input.ndjson
    binds:
      - port_type: stream
        type: fund_load.ports.input_source:InputSource
  output_sink:
    factory: fund_load.adapters.factory:file_output_sink
    settings:
      path: output.txt
    binds:
      - port_type: stream
        type: fund_load.ports.output_sink:OutputSink
  window_store:
    factory: fund_load.adapters.factory:window_store_memory
    settings: {}
    binds:
      - port_type: kv
        type: fund_load.ports.window_store:WindowReadPort
      - port_type: kv
        type: fund_load.ports.window_store:WindowWritePort
  prime_checker:
    factory: fund_load.adapters.factory:prime_checker_stub
    settings:
      max_id: 100
    binds:
      - port_type: kv
        type: fund_load.ports.prime_checker:PrimeChecker
""",
    )

    input_path = tmp_path / "input.ndjson"
    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    exit_code = run(
        [
            "--config",
            str(cfg_path),
            "--input",
            str(input_path),
            "--output",
            str(tmp_path / "output.txt"),
            "--trace-path",
            str(tmp_path / "trace.jsonl"),
        ],
    )

    assert exit_code == 0
    trace_path = tmp_path / "trace.jsonl"
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    first = json.loads(lines[0])
    assert first["step_name"] == "parse_load_attempt"
    assert first["ctx_before"] == {"line_no": 1}


def test_runtime_tracing_disabled_no_sink(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  pipeline:
    - parse_load_attempt
    - compute_time_keys
    - idempotency_gate
    - compute_features
    - evaluate_policies
    - update_windows
    - format_output
    - write_output
  discovery_modules:
    - fund_load.usecases.steps
  tracing:
    enabled: false
nodes:
  compute_time_keys:
    week_start: MON
  compute_features:
    monday_multiplier:
      enabled: false
      multiplier: 2
      apply_to: amount
    prime_gate:
      enabled: false
      global_per_day: 1
      amount_cap: 9999
  evaluate_policies:
    limits:
      daily_amount: 5000
      weekly_amount: 20000
      daily_attempts: 3
    prime_gate:
      enabled: false
      global_per_day: 1
      amount_cap: 9999
  update_windows:
    daily_prime_gate:
      enabled: false
adapters:
  input_source:
    factory: fund_load.adapters.factory:file_input_source
    settings:
      path: input.ndjson
    binds:
      - port_type: stream
        type: fund_load.ports.input_source:InputSource
  output_sink:
    factory: fund_load.adapters.factory:file_output_sink
    settings:
      path: output.txt
    binds:
      - port_type: stream
        type: fund_load.ports.output_sink:OutputSink
  window_store:
    factory: fund_load.adapters.factory:window_store_memory
    settings: {}
    binds:
      - port_type: kv
        type: fund_load.ports.window_store:WindowReadPort
      - port_type: kv
        type: fund_load.ports.window_store:WindowWritePort
  prime_checker:
    factory: fund_load.adapters.factory:prime_checker_stub
    settings:
      max_id: 100
    binds:
      - port_type: kv
        type: fund_load.ports.prime_checker:PrimeChecker
""",
    )

    input_path = tmp_path / "input.ndjson"
    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    exit_code = run(
        [
            "--config",
            str(cfg_path),
            "--input",
            str(input_path),
            "--output",
            str(tmp_path / "output.txt"),
        ],
    )

    assert exit_code == 0
    assert not (tmp_path / "trace.jsonl").exists()
