from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app import run, run_with_registry
from stream_kernel.app.runtime import run_with_config
import stream_kernel.app.runtime as runtime_module
from stream_kernel.execution.orchestration.builder import build_adapter_contracts, build_runtime_artifacts
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import ConfigError, validate_newgen_config
from stream_kernel.adapters.file_io import (
    ByteRecord,
    SinkLine,
    TextRecord,
    egress_file_sink as file_output_sink,
    ingress_file_source as file_input_source,
    sink_file_sink as file_sink_alias,
    source_file_source as file_source_alias,
)
from fund_load.services.window_store import window_store_memory
from stream_kernel.adapters.file_io import FileLineInputSource
from stream_kernel.adapters.file_io import FileOutputSink
from fund_load.services.window_store import WindowStoreService


@adapter(emits=[])
def _plain_factory(settings: dict[str, object]) -> object:
    # Plain decorated factory with empty contracts should be ignored by contract builder.
    return object()


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_runtime_contract_summary_defaults_to_memory_profile() -> None:
    config = {
        "scenario": {"name": "baseline"},
        "runtime": {},
        "nodes": {},
        "adapters": {},
    }
    summary = runtime_module.runtime_contract_summary(config)
    assert summary["execution_ipc"]["enabled"] is False
    assert summary["execution_ipc"]["transport"] is None
    assert summary["process_groups"]["count"] == 0
    assert summary["web"]["interface_count"] == 0
    assert summary["kv_backend"] == "memory"
    assert summary["ordering_sink_mode"] == "completion"
    assert summary["bootstrap_mode"] == "inline"
    assert summary["execution_ipc"]["secret_mode"] is None
    assert summary["execution_ipc"]["kdf"] is None


def test_runtime_contract_summary_reports_phase0_sections() -> None:
    config = {
        "scenario": {"name": "baseline"},
        "runtime": {
            "platform": {
                "kv": {"backend": "memory"},
                "execution_ipc": {
                    "transport": "tcp_local",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "auth": {
                        "mode": "hmac",
                        "secret_mode": "generated",
                        "kdf": "hkdf_sha256",
                        "ttl_seconds": 30,
                    },
                    "max_payload_bytes": 1024,
                },
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "web"}, {"name": "cpu_sync"}],
            },
            "web": {
                "interfaces": [
                    {"kind": "http", "binds": ["request", "response"]},
                    {"kind": "websocket", "binds": ["stream"]},
                ]
            },
            "ordering": {"sink_mode": "source_seq"},
        },
        "nodes": {},
        "adapters": {},
    }
    summary = runtime_module.runtime_contract_summary(config)
    assert summary["execution_ipc"]["enabled"] is True
    assert summary["execution_ipc"]["transport"] == "tcp_local"
    assert summary["execution_ipc"]["auth_mode"] == "hmac"
    assert summary["execution_ipc"]["secret_mode"] == "generated"
    assert summary["execution_ipc"]["kdf"] == "hkdf_sha256"
    assert summary["execution_ipc"]["ttl_seconds"] == 30
    assert summary["bootstrap_mode"] == "process_supervisor"
    assert summary["process_groups"]["names"] == ["web", "cpu_sync"]
    assert summary["web"]["kinds"] == ["http", "websocket"]
    assert summary["ordering_sink_mode"] == "source_seq"


def test_runtime_contract_summary_reports_execution_transport_profile() -> None:
    # IPC-INT-03: runtime summary should expose resolved execution transport profile.
    memory_summary = runtime_module.runtime_contract_summary(
        {
            "scenario": {"name": "baseline"},
            "runtime": {},
            "nodes": {},
            "adapters": {},
        }
    )
    assert memory_summary["execution_transport_profile"] == "memory"

    tcp_summary = runtime_module.runtime_contract_summary(
        {
            "scenario": {"name": "baseline"},
            "runtime": {
                "platform": {
                    "execution_ipc": {
                        "transport": "tcp_local",
                        "bind_host": "127.0.0.1",
                        "bind_port": 0,
                        "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
                        "max_payload_bytes": 1048576,
                    }
                }
            },
            "nodes": {},
            "adapters": {},
        }
    )
    assert tcp_summary["execution_transport_profile"] == "tcp_local"


def test_run_with_config_builds_runtime_contract_summary_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_summary(config: dict[str, object]) -> dict[str, object]:
        captured["summary_called"] = True
        captured["config"] = config
        return {"ok": True}

    monkeypatch.setattr(runtime_module, "runtime_contract_summary", _fake_summary)
    monkeypatch.setattr(
        runtime_module.execution_builder,
        "build_runtime_artifacts",
        lambda *_a, **_k: SimpleNamespace(),
    )
    monkeypatch.setattr(
        runtime_module.execution_builder,
        "execute_runtime_artifacts",
        lambda *_a, **_k: None,
    )

    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {},
        "nodes": {},
        "adapters": {},
    }
    exit_code = run_with_config(cfg)
    assert exit_code == 0
    assert captured["summary_called"] is True
    assert captured["config"] is cfg


def test_build_adapter_contracts_from_factory_metadata() -> None:
    # Adapter contracts are sourced from AdapterRegistry metadata (kind -> @adapter contract).
    from stream_kernel.adapters.file_io import ByteRecord, SinkLine, TextRecord, egress_file_sink as file_output_sink, ingress_file_source as file_input_source

    registry = AdapterRegistry()
    registry.register("ingress_file", "ingress_file", file_input_source)
    registry.register("egress_file", "egress_file", file_output_sink)
    adapters = {
        "ingress_file": {"settings": {}},
        "egress_file": {"settings": {}},
    }
    contracts = build_adapter_contracts(adapters, adapter_registry=registry)
    by_name = {contract.name: contract for contract in contracts}
    assert set(by_name.keys()) == {"ingress_file", "egress_file"}
    assert [token.__name__ for token in by_name["ingress_file"].emits] == ["ByteRecord", "TextRecord"]
    assert [token.__name__ for token in by_name["egress_file"].consumes] == ["SinkLine"]


def test_build_adapter_contracts_from_registry_metadata() -> None:
    # run_with_config path can resolve contracts from AdapterRegistry role/kind metadata.
    from fund_load.domain.messages import RawLine

    registry = AdapterRegistry()

    @adapter(consumes=[], emits=[RawLine])
    def _source_factory(settings: dict[str, object]) -> object:
        return object()

    registry.register("ingress_file", "ingress_file", _source_factory)
    adapters = {"ingress_file": {"settings": {}}}
    contracts = build_adapter_contracts(adapters, adapter_registry=registry)
    assert len(contracts) == 1
    assert contracts[0].name == "ingress_file"
    assert contracts[0].consumes == []
    assert [token.__name__ for token in contracts[0].emits] == ["RawLine"]


def test_build_adapter_contracts_supports_source_sink_alias_names() -> None:
    # Generic role names source/sink resolve to the same file transport contracts.
    registry = AdapterRegistry()
    registry.register("source", "source", file_source_alias)
    registry.register("sink", "sink", file_sink_alias)
    adapters = {
        "source": {"settings": {}},
        "sink": {"settings": {}},
    }
    contracts = build_adapter_contracts(adapters, adapter_registry=registry)
    by_name = {contract.name: contract for contract in contracts}
    assert set(by_name.keys()) == {"source", "sink"}
    assert [token.__name__ for token in by_name["source"].emits] == ["ByteRecord", "TextRecord"]
    assert [token.__name__ for token in by_name["sink"].consumes] == ["SinkLine"]


def test_run_with_config_builds_runtime_and_runs(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  discovery_modules:
    - fund_load
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

    cfg = validate_newgen_config(load_yaml_config(cfg_path))
    input_path = tmp_path / "input.ndjson"
    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "output.txt"

    registry = AdapterRegistry()
    registry.register("ingress_file", "ingress_file", file_input_source)
    registry.register("egress_file", "egress_file", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "ingress_file": [("stream", FileLineInputSource)],
        "egress_file": [("stream", FileOutputSink)],
        "window_store": [("service", WindowStoreService)],    }

    exit_code = run_with_config(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=[
            "fund_load.usecases.steps",
            "fund_load.services.prime_checker",
            "stream_kernel.adapters.file_io",
        ],
        argv_overrides={
            "input": str(input_path),
            "output": str(output_path),
        },
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_with_config_resolves_runtime_wiring_from_config(tmp_path: Path) -> None:
    # Framework-first path: run_with_config should resolve discovery + adapters without manual registry wiring.
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  discovery_modules:
    - fund_load
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
      path: placeholder.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: placeholder.txt
    binds:
      - stream
  window_store:
    settings: {}
    binds:
      - service
""",
    )

    cfg = validate_newgen_config(load_yaml_config(cfg_path))
    input_path = tmp_path / "input.ndjson"
    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "output.txt"

    exit_code = run_with_config(
        cfg,
        argv_overrides={
            "input": str(input_path),
            "output": str(output_path),
        },
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_with_config_works_without_write_output_step_when_sink_adapter_present(tmp_path: Path) -> None:
    # Output persistence should work via graph-native sink adapter even if project write_output step is not discovered.
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
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
      path: placeholder.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: placeholder.txt
    binds:
      - stream
  window_store:
    settings: {}
    binds:
      - service
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
    registry.register("ingress_file", "ingress_file", file_input_source)
    registry.register("egress_file", "egress_file", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "ingress_file": [("stream", FileLineInputSource)],
        "egress_file": [("stream", FileOutputSink)],
        "window_store": [("service", WindowStoreService)],
    }

    exit_code = run_with_config(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=[
            "fund_load.usecases.steps.parse_load_attempt",
            "fund_load.usecases.steps.compute_time_keys",
            "fund_load.usecases.steps.idempotency_gate",
            "fund_load.usecases.steps.compute_features",
            "fund_load.usecases.steps.evaluate_policies",
            "fund_load.usecases.steps.update_windows",
            "fund_load.usecases.steps.format_output",
            "fund_load.usecases.steps.io_bridge",
            "fund_load.services.prime_checker",
            "fund_load.services.window_store",
            "stream_kernel.adapters.file_io",
        ],
        argv_overrides={
            "input": str(input_path),
            "output": str(output_path),
        },
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_build_runtime_artifacts_uses_graph_native_sink_instead_of_write_output_step(
    tmp_path: Path,
) -> None:
    # Stage 4 contract: project execution path should not include write_output;
    # sink adapter should be attached as graph-native sink node.
    cfg_path = _write(
        tmp_path,
        """
version: 1
scenario:
  name: baseline
runtime:
  strict: true
  discovery_modules:
    - fund_load
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
    cfg = validate_newgen_config(load_yaml_config(cfg_path))

    registry = AdapterRegistry()
    registry.register("ingress_file", "ingress_file", file_input_source)
    registry.register("egress_file", "egress_file", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "ingress_file": [("stream", FileLineInputSource)],
        "egress_file": [("stream", FileOutputSink)],
        "window_store": [("service", WindowStoreService)],
    }

    artifacts = build_runtime_artifacts(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
    )
    names = [spec.name for spec in artifacts.scenario.steps]
    assert "write_output" not in names
    assert "sink:egress_file" in names


def test_run_reads_cli_and_overrides_paths(tmp_path: Path) -> None:
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
      path: placeholder.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: placeholder.txt
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
    output_path = tmp_path / "output.txt"

    registry = AdapterRegistry()
    registry.register("ingress_file", "ingress_file", file_input_source)
    registry.register("egress_file", "egress_file", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "ingress_file": [("stream", FileLineInputSource)],
        "egress_file": [("stream", FileOutputSink)],
        "window_store": [("service", WindowStoreService)],    }

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
        discovery_modules=[
            "fund_load.usecases.steps",
            "fund_load.services.prime_checker",
            "stream_kernel.adapters.file_io",
        ],
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").strip() == '{"id":"1","customer_id":"10","accepted":true}'


def test_run_with_registry_is_thin_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guardrail: run_with_registry must only parse/load/validate/apply-overrides and delegate.
    captured: dict[str, object] = {}
    cfg = {"scenario": {"name": "baseline"}, "runtime": {"discovery_modules": []}, "adapters": {}}

    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="cfg.yml", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)

    def _apply_overrides(
        config: dict[str, object],
        _args: object,
        *,
        discovery_modules: list[str] | None = None,
    ) -> None:
        captured["applied"] = True
        captured["config_after_apply"] = config
        captured["overrides_discovery_modules"] = discovery_modules

    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", _apply_overrides)

    registry = AdapterRegistry()
    bindings: dict[str, object] = {}
    discovery = ["m1", "m2"]

    def _delegate(config: dict[str, object], **kwargs: object) -> int:
        captured["delegated_config"] = config
        captured["delegated_kwargs"] = kwargs
        return 77

    monkeypatch.setattr("stream_kernel.app.runtime.run_with_config", _delegate)

    exit_code = run_with_registry(
        ["--config", "cfg.yml"],
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=discovery,
    )

    assert exit_code == 77
    assert captured["applied"] is True
    assert captured["overrides_discovery_modules"] == discovery
    assert captured["delegated_config"] == cfg
    assert captured["delegated_kwargs"] == {
        "adapter_registry": registry,
        "adapter_bindings": bindings,
        "discovery_modules": discovery,
        "argv_overrides": None,
    }


def test_run_uses_factories_and_binds(tmp_path: Path) -> None:
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
    - stream_kernel.adapters.file_io
    - fund_load.services.prime_checker
    - fund_load.services.window_store
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
      path: placeholder.ndjson
    binds:
      - stream
  egress_file:
    settings:
      path: placeholder.txt
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


def test_run_with_config_uses_discovery_order_when_pipeline_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # When runtime.pipeline is absent, runtime should use execution plan from DAG.
    captured: dict[str, object] = {}

    class _Meta:
        def __init__(self, name: str) -> None:
            self.name = name

    class _NodeDef:
        def __init__(self, name: str) -> None:
            self.meta = _Meta(name)

    class _Ctx:
        def __init__(self) -> None:
            self.nodes = [_NodeDef("b"), _NodeDef("a")]

        def discover(self, _modules) -> None:
            return None

        def build_consumer_registry(self):
            return type("R", (), {"list_tokens": lambda *_a: []})()

        def preflight(self, *, strict: bool = True, extra_contracts=None):
            from stream_kernel.kernel.dag import Dag

            # Intentionally opposite to discovery order to assert DAG-driven plan.
            return Dag(nodes=["b", "a"], edges=[("a", "b")])

        def build_scenario(self, *, scenario_id, step_names, wiring):
            captured["step_names"] = list(step_names)
            return type("S", (), {"steps": []})()

    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"ingress_file": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", lambda **_kw: None)

    cfg = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {},
        "nodes": {},
        "adapters": {"ingress_file": {}, "egress_file": {}},
    }
    exit_code = run_with_config(
        cfg,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["step_names"] == ["a", "b"]


def test_run_with_config_invokes_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime must run preflight before scenario build to fail fast on contract violations.
    captured: dict[str, object] = {"preflight_called": False}

    class _Meta:
        def __init__(self, name: str) -> None:
            self.name = name

    class _NodeDef:
        def __init__(self, name: str) -> None:
            self.meta = _Meta(name)

    class _Ctx:
        def __init__(self) -> None:
            self.nodes = [_NodeDef("a")]

        def discover(self, _modules) -> None:
            return None

        def preflight(self, *, strict: bool = True, extra_contracts=None) -> None:
            captured["preflight_called"] = True
            captured["strict"] = strict
            captured["extra_contracts"] = extra_contracts
            from stream_kernel.kernel.dag import Dag

            return Dag(nodes=["a"], edges=[])

        def build_consumer_registry(self):
            return type("R", (), {"list_tokens": lambda *_a: []})()

        def build_scenario(self, *, scenario_id, step_names, wiring):
            return type("S", (), {"steps": []})()

    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"ingress_file": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", lambda **_kw: None)

    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"strict": True},
        "nodes": {},
        "adapters": {"ingress_file": {}, "egress_file": {}},
    }
    exit_code = run_with_config(
        cfg,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["preflight_called"] is True
    assert captured["strict"] is True
    assert captured["extra_contracts"] == []


def test_run_with_config_rejects_runtime_mapping() -> None:
    # runtime must be a mapping for run_with_config (Configuration spec §2.1).
    cfg = {"scenario": {"name": "baseline"}, "runtime": "nope", "adapters": {}}
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        run_with_config(
            cfg,
            adapter_registry=registry,
            adapter_bindings={},
            discovery_modules=[],
        )


def test_run_with_config_rejects_adapters_mapping() -> None:
    # adapters must be a mapping for run_with_config (Configuration spec §2.1).
    cfg = {"scenario": {"name": "baseline"}, "runtime": {}, "adapters": "nope"}
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        run_with_config(
            cfg,
            adapter_registry=registry,
            adapter_bindings={},
            discovery_modules=[],
        )


def test_run_with_config_ignores_runtime_pipeline_without_special_case(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_with_config should not carry dedicated legacy pipeline rejection logic.
    captured: dict[str, object] = {}

    class _Meta:
        def __init__(self, name: str) -> None:
            self.name = name

    class _NodeDef:
        def __init__(self, name: str) -> None:
            self.meta = _Meta(name)

    class _Ctx:
        def __init__(self) -> None:
            self.nodes = [_NodeDef("b"), _NodeDef("a")]

        def discover(self, _modules) -> None:
            return None

        def build_consumer_registry(self):
            return type("R", (), {"list_tokens": lambda *_a: []})()

        def preflight(self, *, strict: bool = True, extra_contracts=None):
            from stream_kernel.kernel.dag import Dag

            return Dag(nodes=["b", "a"], edges=[("a", "b")])

        def build_scenario(self, *, scenario_id, step_names, wiring):
            captured["step_names"] = list(step_names)
            return type("S", (), {"steps": []})()

    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"ingress_file": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", lambda **_kw: None)

    cfg = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"pipeline": ["b", "a"]},
        "nodes": {},
        "adapters": {"ingress_file": {}, "egress_file": {}},
    }
    exit_code = run_with_config(
        cfg,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["step_names"] == ["a", "b"]


def test_run_rejects_invalid_runtime_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime mapping is required (Configuration spec §2.1).
    cfg = {"scenario": {"name": "baseline"}, "runtime": "nope", "adapters": {}}
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)

    with pytest.raises(ValueError):
        run(["--config", "cfg.yml"])


def test_run_rejects_invalid_discovery_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    # discovery_modules must be a list of strings (Configuration spec §2.1).
    cfg = {"scenario": {"name": "baseline"}, "runtime": {"discovery_modules": "nope"}, "adapters": {}}
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)

    with pytest.raises(ValueError):
        run(["--config", "cfg.yml"])


def test_run_rejects_non_mapping_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    # Adapter configs must be a mapping (Configuration spec §2.1).
    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": "nope",
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", lambda *_a, **_k: None)

    with pytest.raises(ValueError):
        run(["--config", "cfg.yml"])


def test_run_rejects_runtime_pipeline_via_runtime_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime key allow-list rejects legacy runtime.pipeline during validation.
    cfg = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"pipeline": ["a", "b"]},
        "nodes": {},
        "adapters": {"ingress_file": {"binds": []}},
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)

    with pytest.raises(ConfigError):
        run(["--config", "cfg.yml"])


def test_run_uses_discovery_order_when_pipeline_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # `run` should derive scenario order from DAG execution plan when pipeline is omitted.
    captured: dict[str, object] = {}

    class _Meta:
        def __init__(self, name: str) -> None:
            self.name = name

    class _NodeDef:
        def __init__(self, name: str) -> None:
            self.meta = _Meta(name)

    class _Ctx:
        def __init__(self) -> None:
            self.nodes = [_NodeDef("step2"), _NodeDef("step1")]

        def discover(self, _modules) -> None:
            return None

        def build_consumer_registry(self):
            return type("R", (), {"list_tokens": lambda *_a: []})()

        def preflight(self, *, strict: bool = True, extra_contracts=None):
            from stream_kernel.kernel.dag import Dag

            return Dag(nodes=["step2", "step1"], edges=[("step1", "step2")])

        def build_scenario(self, *, scenario_id, step_names, wiring):
            captured["step_names"] = list(step_names)
            return type("S", (), {"steps": []})()

    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {"ingress_file": {}, "egress_file": {}},
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"ingress_file": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", lambda **_kw: None)

    exit_code = run(["--config", "cfg.yml"])
    assert exit_code == 0
    assert captured["step_names"] == ["step1", "step2"]


def test_run_requires_source_adapter_with_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime requires at least one source adapter exposing read() (source nodes bootstrap rule).
    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {
            "egress_file": {
                "settings": {},
                "binds": [],
            }
        },
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"egress_file": object()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.ApplicationContext",
        lambda: type(
            "C",
            (),
            {
                "discover": lambda *_a, **_k: None,
                "preflight": lambda *_a, **_k: None,
                "build_scenario": lambda *_a, **_k: type("S", (), {"steps": []})(),
                "build_consumer_registry": lambda *_a, **_k: type("R", (), {"list_tokens": lambda *_a: []})(),
            },
        )(),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.SyncRunner",
        lambda **_k: type(
            "R",
            (),
            {
                "run": lambda *_a, **_k: None,
                "run_inputs": lambda *_a, **_k: None,
                "on_run_end": lambda *_a, **_k: None,
            },
        )(),
    )

    with pytest.raises(ValueError):
        run(["--config", "cfg.yml"])


def test_run_accepts_non_default_source_role_with_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime must not depend on hardcoded adapter role names; any read-capable adapter can be source.
    captured: dict[str, object] = {}

    class _Meta:
        def __init__(self, name: str) -> None:
            self.name = name

    class _NodeDef:
        def __init__(self, name: str) -> None:
            self.meta = _Meta(name)

    class _Ctx:
        def __init__(self) -> None:
            self.nodes = [_NodeDef("parse")]

        def discover(self, _modules) -> None:
            return None

        def preflight(self, *, strict: bool = True, extra_contracts=None):
            from stream_kernel.kernel.dag import Dag

            return Dag(nodes=["parse"], edges=[])

        def build_consumer_registry(self):
            return type("R", (), {"list_tokens": lambda *_a: []})()

        def build_scenario(self, *, scenario_id, step_names, wiring):
            captured["step_names"] = list(step_names)
            return type("S", (), {"steps": []})()

    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {"events_source": {}},
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"events_source": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", lambda **_kw: None)

    exit_code = run(["--config", "cfg.yml"])
    assert exit_code == 0
    assert captured["step_names"] == ["parse"]
