from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

# Experimental scenario validates Monday multiplier + prime gate (docs/implementation/steps/04-06).
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
    # Test-only sink node to inspect decisions without token self-loop.
    DECISIONS.append(msg)
    return []


@dataclass(frozen=True, slots=True)
class _InMemoryInputSource:
    # InputSource stub for deterministic input order (Challenge task NDJSON).
    lines: list[RawLine]

    def read(self) -> Iterable[RawLine]:
        return self.lines


@dataclass(frozen=True, slots=True)
class _CollectingOutputSink(OutputSink):
    # OutputSink stub to assert output rows and order.
    lines: list[str]

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        pass


def _config(lines: list[RawLine]) -> dict[str, object]:
    # Newgen config uses legacy global sections to satisfy current step config paths.
    # NOTE: This mirrors current step code rather than the node-slice ideal in docs.
    return {
        "version": 1,
        "scenario": {"name": "exp_mp"},
        "runtime": {
            "strict": True,
            "discovery_modules": ["fund_load.usecases.steps", __name__],
        },
        "nodes": {
            "compute_time_keys": {"week_start": "MON"},
            "compute_features": {
                "monday_multiplier": {
                    "enabled": True,
                    "multiplier": Decimal("2.0"),
                    "apply_to": "amount",
                },
                "prime_gate": {"enabled": True, "global_per_day": 1, "amount_cap": Decimal("10000.00")},
            },
            "evaluate_policies": {
                "limits": {
                    "daily_amount": Decimal("20000.00"),
                    "weekly_amount": Decimal("50000.00"),
                    "daily_attempts": 3,
                },
                "prime_gate": {"enabled": True, "global_per_day": 1, "amount_cap": Decimal("10000.00")},
            },
            "update_windows": {"daily_prime_gate": {"enabled": True}},
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
            "prime_checker": {"settings": {"max_id": 20000}},
        },
    }


def _raw_line(line_no: int, *, id_value: str, customer_id: str, amount: str, ts: str) -> RawLine:
    payload = {
        "id": id_value,
        "customer_id": customer_id,
        "load_amount": amount,
        "time": ts,
    }
    return RawLine(line_no=line_no, raw_text=json.dumps(payload))


def test_experiment_end_to_end_prime_and_monday() -> None:
    # Monday multiplier affects effective amount; prime gate blocks large primes and limits daily count.
    DECISIONS.clear()
    inputs = [
        # Monday + prime id -> exceeds prime amount cap after multiplier (decline).
        _raw_line(1, id_value="101", customer_id="10", amount="$6000.00", ts="2025-01-06T10:00:00Z"),
        # Monday + non-prime id -> still under daily limit (accept).
        _raw_line(2, id_value="102", customer_id="10", amount="$6000.00", ts="2025-01-06T11:00:00Z"),
        # Tuesday + prime id -> accepted (first prime of the day).
        _raw_line(3, id_value="103", customer_id="10", amount="$4000.00", ts="2025-01-07T09:00:00Z"),
        # Tuesday + another prime -> rejected by prime daily global limit.
        _raw_line(4, id_value="107", customer_id="10", amount="$1000.00", ts="2025-01-07T10:00:00Z"),
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
    registry.register(
        "prime_checker",
        "prime_checker",
        lambda settings: SievePrimeChecker.from_max(int(settings["max_id"])),
    )

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
    assert decision_by_id["101"].accepted is False
    assert decision_by_id["101"].reasons == (ReasonCode.PRIME_AMOUNT_CAP.value,)
    assert decision_by_id["102"].accepted is True
    assert decision_by_id["107"].accepted is False
    assert decision_by_id["107"].reasons == (ReasonCode.PRIME_DAILY_GLOBAL_LIMIT.value,)

    output = [json.loads(line) for line in output_sink.lines]
    assert [row["id"] for row in output] == ["101", "102", "103", "107"]
    assert output[0]["accepted"] is False
    assert output[1]["accepted"] is True
