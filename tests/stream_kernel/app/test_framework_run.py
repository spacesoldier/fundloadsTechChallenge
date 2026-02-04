from __future__ import annotations

from pathlib import Path

import pytest

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app import run, run_with_registry
from stream_kernel.app.runtime import run_with_config
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from fund_load.adapters.input_source import FileInputSource
from fund_load.adapters.output_sink import FileOutputSink
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.ports.input_source import InputSource
from fund_load.ports.output_sink import OutputSink
from fund_load.ports.window_store import WindowReadPort, WindowWritePort
from fund_load.ports.prime_checker import PrimeChecker


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_run_with_config_builds_runtime_and_runs(tmp_path: Path) -> None:
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
    kind: file
    factory: fund_load.adapters.factory:file_input_source
    settings:
      path: input.ndjson
    binds:
      - port_type: stream
        type: fund_load.ports.input_source:InputSource
  output_sink:
    kind: file
    factory: fund_load.adapters.factory:file_output_sink
    settings:
      path: output.txt
    binds:
      - port_type: stream
        type: fund_load.ports.output_sink:OutputSink
  window_store:
    kind: memory
    factory: fund_load.adapters.factory:window_store_memory
    settings: {}
    binds:
      - port_type: kv
        type: fund_load.ports.window_store:WindowReadPort
      - port_type: kv
        type: fund_load.ports.window_store:WindowWritePort
  prime_checker:
    kind: stub
    factory: fund_load.adapters.factory:prime_checker_stub
    settings:
      strategy: sieve
      max_id: 100
    binds:
      - port_type: kv
        type: fund_load.ports.prime_checker:PrimeChecker
""",
    )

    cfg = validate_newgen_config(load_yaml_config(cfg_path))
    input_path = tmp_path / "input.ndjson"
    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "output.txt"

    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: FileInputSource(Path(settings["path"])))
    registry.register("output_sink", "file", lambda settings: FileOutputSink(Path(settings["path"])))
    registry.register("window_store", "memory", lambda settings: InMemoryWindowStore())
    registry.register(
        "prime_checker",
        "stub",
        lambda settings: SievePrimeChecker.from_max(int(settings["max_id"])),
    )

    bindings = {
        "input_source": [("stream", InputSource)],
        "output_sink": [("stream", OutputSink)],
        "window_store": [("kv", WindowReadPort), ("kv", WindowWritePort)],
        "prime_checker": [("kv", PrimeChecker)],
    }

    exit_code = run_with_config(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=["fund_load.usecases.steps"],
        argv_overrides={
            "input": str(input_path),
            "output": str(output_path),
        },
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_reads_cli_and_overrides_paths(tmp_path: Path) -> None:
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
    kind: file
    factory: fund_load.adapters.factory:file_input_source
    settings:
      path: placeholder.ndjson
    binds:
      - port_type: stream
        type: fund_load.ports.input_source:InputSource
  output_sink:
    kind: file
    factory: fund_load.adapters.factory:file_output_sink
    settings:
      path: placeholder.txt
    binds:
      - port_type: stream
        type: fund_load.ports.output_sink:OutputSink
  window_store:
    kind: memory
    factory: fund_load.adapters.factory:window_store_memory
    settings: {}
    binds:
      - port_type: kv
        type: fund_load.ports.window_store:WindowReadPort
      - port_type: kv
        type: fund_load.ports.window_store:WindowWritePort
  prime_checker:
    kind: stub
    factory: fund_load.adapters.factory:prime_checker_stub
    settings:
      strategy: sieve
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
    output_path = tmp_path / "output.txt"

    registry = AdapterRegistry()
    registry.register("input_source", "file", lambda settings: FileInputSource(Path(settings["path"])))
    registry.register("output_sink", "file", lambda settings: FileOutputSink(Path(settings["path"])))
    registry.register("window_store", "memory", lambda settings: InMemoryWindowStore())
    registry.register(
        "prime_checker",
        "stub",
        lambda settings: SievePrimeChecker.from_max(int(settings["max_id"])),
    )

    bindings = {
        "input_source": [("stream", InputSource)],
        "output_sink": [("stream", OutputSink)],
        "window_store": [("kv", WindowReadPort), ("kv", WindowWritePort)],
        "prime_checker": [("kv", PrimeChecker)],
    }

    exit_code = run_with_registry(
        [
            "--config",
            str(cfg_path),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=["fund_load.usecases.steps"],
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_uses_factories_and_binds(tmp_path: Path) -> None:
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
      path: placeholder.ndjson
    binds:
      - port_type: stream
        type: fund_load.ports.input_source:InputSource
  output_sink:
    factory: fund_load.adapters.factory:file_output_sink
    settings:
      path: placeholder.txt
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
    output_path = tmp_path / "output.txt"

    exit_code = run(
        [
            "--config",
            str(cfg_path),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_with_config_fails_without_pipeline() -> None:
    cfg = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {},
        "nodes": {},
        "adapters": {},
    }
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        run_with_config(
            cfg,
            adapter_registry=registry,
            adapter_bindings={},
            discovery_modules=[],
            argv_overrides={},
        )
