from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

# End-to-end baseline scenario follows docs/implementation/steps/* and docs/Challenge task.md.
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.domain.messages import RawLine
from fund_load.domain.reasons import ReasonCode
from fund_load.ports.output_sink import OutputSink
from fund_load.ports.prime_checker import PrimeChecker
from fund_load.ports.window_store import WindowReadPort, WindowWritePort
from fund_load.usecases.messages import Decision
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import run_with_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.kernel.node import node


DECISIONS: list[Decision] = []


@node(name="record_decisions")
def record_decisions(msg: Decision, ctx: object | None) -> list[Decision]:
    # Test-only node: capture Decisions after EvaluatePolicies (Step 05).
    DECISIONS.append(msg)
    return [msg]


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
            "pipeline": [
                "parse_load_attempt",
                "compute_time_keys",
                "idempotency_gate",
                "compute_features",
                "evaluate_policies",
                "record_decisions",
                "update_windows",
                "format_output",
                "write_output",
            ],
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
            "input_source": {"kind": "memory", "settings": {"lines": lines}},
            "output_sink": {
                "kind": "memory",
                "settings": {},
                "factory": "tests.integration.test_end_to_end_baseline_limits:noop",
                "binds": [{"port_type": "stream", "type": "fund_load.ports.output_sink:OutputSink"}],
            },
            "window_store": {"kind": "memory", "settings": {}},
            "prime_checker": {"kind": "stub", "settings": {"max_id": 0}},
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
    registry.register("input_source", "memory", lambda settings: _InMemoryInputSource(settings["lines"]))
    registry.register("output_sink", "memory", lambda settings, _sink=output_sink: _sink)
    registry.register("window_store", "memory", lambda settings: InMemoryWindowStore())
    registry.register("prime_checker", "stub", lambda settings: SievePrimeChecker.from_max(0))

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
