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
  discovery_modules:
    - fund_load.usecases.steps
    - fund_load.adapters.io
    - fund_load.services.prime_checker
    - fund_load.services.window_store
  tracing:
    enabled: true
    signature:
      mode: type_only
    context_diff:
      mode: whitelist
      whitelist:
        - run_id
    sink:
      name: trace_jsonl
      settings:
        path: trace.jsonl
        write_mode: line
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
  ingress_file:
    settings:
      path: input.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: output.txt
    binds:
      - stream
  window_store:
    settings: {}
    binds:
      - service
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
    # Source adapters now execute as graph-native bootstrap nodes and are traced as regular steps.
    assert len(lines) == 9
    first = json.loads(lines[0])
    assert first["step_name"] == "source:ingress_file"
    assert first["ctx_before"] == {"run_id": "run"}
    second = json.loads(lines[1])
    assert second["step_name"] == "parse_load_attempt"


def test_runtime_tracing_disabled_no_sink(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  discovery_modules:
    - fund_load.usecases.steps
    - fund_load.adapters.io
    - fund_load.services.prime_checker
    - fund_load.services.window_store
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
  ingress_file:
    settings:
      path: input.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: output.txt
    binds:
      - stream
  window_store:
    settings: {}
    binds:
      - service
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
