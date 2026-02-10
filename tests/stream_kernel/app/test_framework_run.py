from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app import run, run_with_registry
from stream_kernel.app.runtime import run_with_config
import stream_kernel.app.runtime as runtime_module
from stream_kernel.execution.builder import build_adapter_contracts
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from fund_load.adapters.io import file_input_source, file_output_sink
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


def test_build_adapter_contracts_from_factory_metadata() -> None:
    # Adapter contracts are sourced from AdapterRegistry metadata (kind -> @adapter contract).
    from fund_load.adapters.io import file_input_source, file_output_sink

    registry = AdapterRegistry()
    registry.register("input_source", "input_source", file_input_source)
    registry.register("output_sink", "output_sink", file_output_sink)
    adapters = {
        "input_source": {"settings": {}},
        "output_sink": {"settings": {}},
    }
    contracts = build_adapter_contracts(adapters, adapter_registry=registry)
    by_name = {contract.name: contract for contract in contracts}
    assert set(by_name.keys()) == {"adapter:input_source", "adapter:output_sink"}
    assert [token.__name__ for token in by_name["adapter:input_source"].emits] == ["RawLine"]
    assert [token.__name__ for token in by_name["adapter:output_sink"].consumes] == ["OutputLine"]


def test_build_adapter_contracts_from_registry_metadata() -> None:
    # run_with_config path can resolve contracts from AdapterRegistry role/kind metadata.
    from fund_load.domain.messages import RawLine

    registry = AdapterRegistry()

    @adapter(consumes=[], emits=[RawLine])
    def _source_factory(settings: dict[str, object]) -> object:
        return object()

    registry.register("input_source", "input_source", _source_factory)
    adapters = {"input_source": {"settings": {}}}
    contracts = build_adapter_contracts(adapters, adapter_registry=registry)
    assert len(contracts) == 1
    assert contracts[0].name == "adapter:input_source"
    assert contracts[0].consumes == []
    assert [token.__name__ for token in contracts[0].emits] == ["RawLine"]


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
    settings:
      path: input.ndjson
    binds:
      - stream
  output_sink:
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
    registry.register("input_source", "input_source", file_input_source)
    registry.register("output_sink", "output_sink", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "input_source": [("stream", FileLineInputSource)],
        "output_sink": [("stream", FileOutputSink)],
        "window_store": [("service", WindowStoreService)],    }

    exit_code = run_with_config(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=[
            "fund_load.usecases.steps",
            "fund_load.services.prime_checker",
            "fund_load.adapters.io",
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
  input_source:
    settings:
      path: placeholder.ndjson
    binds:
      - stream
  output_sink:
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
  input_source:
    settings:
      path: placeholder.ndjson
    binds:
      - stream
  output_sink:
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
    registry.register("input_source", "input_source", file_input_source)
    registry.register("output_sink", "output_sink", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "input_source": [("stream", FileLineInputSource)],
        "output_sink": [("stream", FileOutputSink)],
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
            "fund_load.services.prime_checker",
            "fund_load.services.window_store",
            "fund_load.adapters.io",
        ],
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
    settings:
      path: placeholder.ndjson
    binds:
      - stream
  output_sink:
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
    registry.register("input_source", "input_source", file_input_source)
    registry.register("output_sink", "output_sink", file_output_sink)
    registry.register("window_store", "window_store", window_store_memory)
    bindings = {
        "input_source": [("stream", FileLineInputSource)],
        "output_sink": [("stream", FileOutputSink)],
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
            "fund_load.adapters.io",
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
    - fund_load.adapters.io
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
  input_source:
    settings:
      path: placeholder.ndjson
    binds:
      - stream
  output_sink:
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

    monkeypatch.setattr("stream_kernel.execution.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.builder.run_with_sync_runner", lambda **_kw: None)

    cfg = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {},
        "nodes": {},
        "adapters": {"input_source": {}, "output_sink": {}},
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

    monkeypatch.setattr("stream_kernel.execution.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.builder.run_with_sync_runner", lambda **_kw: None)

    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"strict": True},
        "nodes": {},
        "adapters": {"input_source": {}, "output_sink": {}},
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


def test_run_with_config_rejects_runtime_pipeline() -> None:
    # runtime.pipeline is removed; runtime must build flow from discovered contracts.
    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"pipeline": ["a", "b"]},
        "adapters": {"input_source": {}, "output_sink": {}},
    }
    with pytest.raises(ValueError):
        run_with_config(
            cfg,
            adapter_registry=AdapterRegistry(),
            adapter_bindings={},
            discovery_modules=[],
        )


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


def test_run_rejects_runtime_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime.pipeline is deprecated and unsupported; routing must derive flow from contracts.
    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": [], "pipeline": ["a", "b"]},
        "adapters": {},
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "stream_kernel.execution.builder.ApplicationContext",
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
        "stream_kernel.execution.builder.SyncRunner",
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
        "adapters": {"input_source": {}, "output_sink": {}},
    }
    monkeypatch.setattr(
        "stream_kernel.app.runtime.parse_args",
        lambda _argv: SimpleNamespace(config="x", input=None, output=None, tracing=None, trace_path=None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime.load_yaml_config", lambda _path: cfg)
    monkeypatch.setattr("stream_kernel.app.runtime.validate_newgen_config", lambda raw: raw)
    monkeypatch.setattr("stream_kernel.app.runtime.apply_cli_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr("stream_kernel.execution.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.builder.run_with_sync_runner", lambda **_kw: None)

    exit_code = run(["--config", "cfg.yml"])
    assert exit_code == 0
    assert captured["step_names"] == ["step1", "step2"]


def test_run_requires_source_adapter_with_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime requires at least one source adapter exposing read() (source nodes bootstrap rule).
    cfg = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {
            "output_sink": {
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
        "stream_kernel.execution.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"output_sink": object()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.ApplicationContext",
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
        "stream_kernel.execution.builder.SyncRunner",
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
    monkeypatch.setattr("stream_kernel.execution.builder.ApplicationContext", _Ctx)
    monkeypatch.setattr(
        "stream_kernel.execution.builder.resolve_runtime_adapters",
        lambda **_kw: (AdapterRegistry(), {}),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"events_source": type("I", (), {"read": lambda *_a: []})()},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.execution.builder.build_execution_observers", lambda *_a, **_k: [])
    monkeypatch.setattr("stream_kernel.execution.builder.run_with_sync_runner", lambda **_kw: None)

    exit_code = run(["--config", "cfg.yml"])
    assert exit_code == 0
    assert captured["step_names"] == ["parse"]
