from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

# End-to-end baseline scenario follows docs/implementation/steps/* and docs/Challenge task.md.
from fund_load.adapters.services.prime_checker import SievePrimeChecker
from fund_load.adapters.state.window_store import InMemoryWindowStore
from fund_load.domain.messages import RawLine
from fund_load.domain.reasons import ReasonCode
from fund_load.ports.output_sink import OutputSink
from fund_load.ports.prime_checker import PrimeChecker
from fund_load.ports.window_store import WindowReadPort, WindowWritePort
from fund_load.usecases.messages import Decision, OutputLine
from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import run_with_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.kernel.node import node


DECISIONS: list[Decision] = []


@node(name="record_decisions", consumes=[Decision], emits=[])
def record_decisions(msg: Decision, ctx: object | None) -> list[Decision]:
    # Test-only sink node: capture decisions without re-emitting the same token.
    DECISIONS.append(msg)
    return []


@dataclass(frozen=True, slots=True)
class _InMemoryInputSource:
    # InputSource stub returns provided RawLine stream (NDJSON order preserved).
    lines: list[RawLine]

    def read(self) -> Iterable[RawLine]:
        return self.lines


@dataclass(frozen=True, slots=True)
class _CollectingOutputSink(OutputSink):
    # OutputSink stub collects output lines for assertions (Output schema in task text).
    lines: list[str]

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        pass


def _config(lines: list[RawLine]) -> dict[str, object]:
    # Newgen config with legacy global sections to match current step config paths.
    # NOTE: Framework docs prefer node slices, but step implementations still read
    # features/policies/windows at the global level. We follow the code path here.
    return {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "strict": True,
            "discovery_modules": ["fund_load.usecases.steps", __name__],
        },
        "nodes": {
            "compute_time_keys": {"week_start": "MON"},
            "compute_features": {
                "monday_multiplier": {
                    "enabled": False,
                    "multiplier": Decimal("2.0"),
                    "apply_to": "amount",
                },
                "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": Decimal("9999.00")},
            },
            "evaluate_policies": {
                "limits": {
                    "daily_amount": Decimal("5000.00"),
                    "weekly_amount": Decimal("20000.00"),
                    "daily_attempts": 3,
                },
                "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": Decimal("9999.00")},
            },
            "update_windows": {"daily_prime_gate": {"enabled": False}},
        },
        "adapters": {
            "input_source": {
                "settings": {"lines": lines},
            },
            "output_sink": {
                "settings": {},
                "binds": ["stream"],
            },
            "window_store": {"settings": {}},
            "prime_checker": {"settings": {"max_id": 0}},
        },
    }


def _raw_line(line_no: int, *, id_value: str, customer_id: str, amount: str, ts: str) -> RawLine:
    # NDJSON line schema is defined in docs/Challenge task.md.
    payload = {
        "id": id_value,
        "customer_id": customer_id,
        "load_amount": amount,
        "time": ts,
    }
    return RawLine(line_no=line_no, raw_text=json.dumps(payload))


def test_baseline_end_to_end_limits() -> None:
    # Baseline rules: per-customer daily/weekly limits and daily attempts (Steps 05-06).
    DECISIONS.clear()
    inputs = [
        # Customer 101: daily attempt limit on 4th attempt.
        _raw_line(1, id_value="1001", customer_id="101", amount="USD100.00", ts="2025-01-01T10:00:00Z"),
        _raw_line(2, id_value="1002", customer_id="101", amount="USD100.00", ts="2025-01-01T11:00:00Z"),
        _raw_line(3, id_value="1003", customer_id="101", amount="USD100.00", ts="2025-01-01T12:00:00Z"),
        _raw_line(4, id_value="1004", customer_id="101", amount="USD100.00", ts="2025-01-01T13:00:00Z"),
        # Customer 202: daily amount limit exceeded on 2nd attempt.
        _raw_line(5, id_value="2001", customer_id="202", amount="$3000.00", ts="2025-01-02T10:00:00Z"),
        _raw_line(6, id_value="2002", customer_id="202", amount="$2500.00", ts="2025-01-02T11:00:00Z"),
        # Customer 303: weekly limit exceeded on 5th day in the same week.
        _raw_line(7, id_value="3001", customer_id="303", amount="USD5000.00", ts="2025-01-06T10:00:00Z"),
        _raw_line(8, id_value="3002", customer_id="303", amount="USD5000.00", ts="2025-01-07T10:00:00Z"),
        _raw_line(9, id_value="3003", customer_id="303", amount="USD5000.00", ts="2025-01-08T10:00:00Z"),
        _raw_line(10, id_value="3004", customer_id="303", amount="USD5000.00", ts="2025-01-09T10:00:00Z"),
        _raw_line(11, id_value="3005", customer_id="303", amount="USD1000.00", ts="2025-01-10T10:00:00Z"),
    ]

    cfg = validate_newgen_config(_config(inputs))

    output_sink = _CollectingOutputSink([])
    registry = AdapterRegistry()

    @adapter(consumes=[], emits=[RawLine])
    def _input_source_factory(settings: dict[str, object]) -> _InMemoryInputSource:
        # Source adapter contract emits RawLine into DAG.
        return _InMemoryInputSource(settings["lines"])  # type: ignore[index]

    @adapter(consumes=[OutputLine], emits=[])
    def _output_sink_factory(settings: dict[str, object], _sink=output_sink) -> _CollectingOutputSink:
        # Sink adapter contract consumes OutputLine from DAG.
        return _sink

    registry.register("input_source", "input_source", _input_source_factory)
    registry.register("output_sink", "output_sink", _output_sink_factory)
    registry.register("window_store", "window_store", lambda settings: InMemoryWindowStore())
    registry.register("prime_checker", "prime_checker", lambda settings: SievePrimeChecker.from_max(0))

    bindings = {
        "output_sink": [("stream", OutputSink)],
        "window_store": [("kv", WindowReadPort), ("kv", WindowWritePort)],
        "prime_checker": [("kv", PrimeChecker)],
    }

    exit_code = run_with_config(
        cfg,
        adapter_registry=registry,
        adapter_bindings=bindings,
        discovery_modules=["fund_load.usecases.steps", __name__],
    )
    assert exit_code == 0

    decision_by_id = {d.id: d for d in DECISIONS}
    assert decision_by_id["1001"].accepted is True
    assert decision_by_id["1002"].accepted is True
    assert decision_by_id["1003"].accepted is True
    assert decision_by_id["1004"].accepted is False
    assert decision_by_id["1004"].reasons == (ReasonCode.DAILY_ATTEMPT_LIMIT.value,)

    assert decision_by_id["2001"].accepted is True
    assert decision_by_id["2002"].accepted is False
    assert decision_by_id["2002"].reasons == (ReasonCode.DAILY_AMOUNT_LIMIT.value,)

    assert decision_by_id["3001"].accepted is True
    assert decision_by_id["3002"].accepted is True
    assert decision_by_id["3003"].accepted is True
    assert decision_by_id["3004"].accepted is True
    assert decision_by_id["3005"].accepted is False
    assert decision_by_id["3005"].reasons == (ReasonCode.WEEKLY_AMOUNT_LIMIT.value,)

    output = [json.loads(line) for line in output_sink.lines]
    assert [row["id"] for row in output] == [str(i) for i in range(1001, 1005)] + [
        "2001",
        "2002",
        "3001",
        "3002",
        "3003",
        "3004",
        "3005",
    ]
    assert output[-1]["accepted"] is False
