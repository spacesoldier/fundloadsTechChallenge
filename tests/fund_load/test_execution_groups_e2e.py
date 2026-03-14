from __future__ import annotations

import json
from datetime import UTC, datetime

from fund_load.domain.messages import LoadAttempt
from fund_load.domain.money import parse_money
from fund_load.usecases.messages import EnrichedAttempt, WindowedDecision
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import (
    build_bootstrap_key_bundle,
)
from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime import (
    execute_child_boundary_loop_from_bundle,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
    ChildBootstrapBundle,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.adapters.file_io import SinkLine
from stream_kernel.routing.envelope import Envelope


def _runtime_for_group(*, group_name: str, nodes: list[str]) -> dict[str, object]:
    return {
        "strict": True,
        "discovery_modules": ["fund_load"],
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {
                    "mode": "hmac",
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                    "ttl_seconds": 30,
                    "nonce_cache_size": 1000,
                },
                "max_payload_bytes": 1048576,
            },
            "process_groups": [
                {
                    "name": group_name,
                    "workers": 1,
                    "nodes": list(nodes),
                }
            ],
        },
    }


def _bundle_for_group(
    *,
    scenario_id: str,
    group_name: str,
    nodes: list[str],
    adapters: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> ChildBootstrapBundle:
    runtime = _runtime_for_group(group_name=group_name, nodes=nodes)
    key_bundle = build_bootstrap_key_bundle(
        runtime,
        token_bytes_fn=lambda n: b"k" * n,
        now_fn=lambda: 17,
    )
    return ChildBootstrapBundle(
        scenario_id=scenario_id,
        process_group=group_name,
        discovery_modules=["fund_load"],
        runtime=runtime,
        adapters=adapters,
        config=config,
        key_bundle=key_bundle,
    )


def _make_attempt(*, line_no: int, id_value: str) -> LoadAttempt:
    return LoadAttempt(
        line_no=line_no,
        id=id_value,
        customer_id="10",
        amount=parse_money("$10.00"),
        ts=datetime(2025, 1, 1, 0, 0, line_no, tzinfo=UTC),
        raw=None,
    )


def _envelopes_with_payload_type(
    outputs: list[object],
    payload_type: type[object],
) -> list[Envelope]:
    result: list[Envelope] = []
    for item in outputs:
        if not isinstance(item, Envelope):
            continue
        if isinstance(item.payload, payload_type):
            result.append(item)
    return result


def _to_boundary_inputs(
    *,
    envelopes: list[Envelope],
    dispatch_group: str,
    target: str,
) -> list[BoundaryDispatchInput]:
    return [
        BoundaryDispatchInput(
            payload=item.payload,
            dispatch_group=dispatch_group,
            target=target,
            trace_id=item.trace_id,
            reply_to=item.reply_to,
            tombstone=item.tombstone,
        )
        for item in envelopes
    ]


def _input_line(idx: int) -> str:
    return json.dumps(
        {
            "id": str(idx),
            "customer_id": "10",
            "load_amount": "$10.00",
            "time": f"2025-01-01T00:00:{idx:02d}Z",
        }
    )


def test_features_group_e2e_emits_exactly_one_enriched_attempt_per_input() -> None:
    bundle = _bundle_for_group(
        scenario_id="fund_load_features_e2e",
        group_name="execution.features",
        nodes=["compute_time_keys", "idempotency_gate", "compute_features"],
    )
    inputs = [
        BoundaryDispatchInput(
            payload=_make_attempt(line_no=i, id_value=str(i)),
            dispatch_group="execution.features",
            target="compute_time_keys",
            tombstone=(i == 4),
        )
        for i in range(1, 5)
    ]

    outputs = execute_child_boundary_loop_from_bundle(bundle=bundle, inputs=inputs)
    enriched = _envelopes_with_payload_type(outputs, EnrichedAttempt)

    assert len(enriched) == 4
    assert [item.payload.base.base.attempt.line_no for item in enriched] == [1, 2, 3, 4]
    assert [item.payload.base.base.attempt.id for item in enriched] == ["1", "2", "3", "4"]
    assert enriched[-1].tombstone is True
    assert all(item.tombstone is False for item in enriched[:-1])


def test_policy_group_e2e_emits_windowed_decision_for_each_input() -> None:
    features_bundle = _bundle_for_group(
        scenario_id="fund_load_policy_e2e_prep",
        group_name="execution.features",
        nodes=["compute_time_keys", "idempotency_gate", "compute_features"],
    )
    features_inputs = [
        BoundaryDispatchInput(
            payload=_make_attempt(line_no=i, id_value=str(i)),
            dispatch_group="execution.features",
            target="compute_time_keys",
            tombstone=(i == 5),
        )
        for i in range(1, 6)
    ]
    features_outputs = execute_child_boundary_loop_from_bundle(
        bundle=features_bundle,
        inputs=features_inputs,
    )
    enriched_inputs = _to_boundary_inputs(
        envelopes=_envelopes_with_payload_type(features_outputs, EnrichedAttempt),
        dispatch_group="execution.policy",
        target="evaluate_policies",
    )

    policy_bundle = _bundle_for_group(
        scenario_id="fund_load_policy_e2e",
        group_name="execution.policy",
        nodes=["evaluate_policies", "update_windows"],
    )
    policy_outputs = execute_child_boundary_loop_from_bundle(
        bundle=policy_bundle,
        inputs=enriched_inputs,
    )
    decisions = _envelopes_with_payload_type(policy_outputs, WindowedDecision)

    assert len(decisions) == 5
    assert [item.payload.line_no for item in decisions] == [1, 2, 3, 4, 5]
    assert decisions[-1].tombstone is True
    assert all(item.tombstone is False for item in decisions[:-1])


def test_egress_group_e2e_emits_exactly_one_sink_line_per_windowed_decision() -> None:
    bundle = _bundle_for_group(
        scenario_id="fund_load_egress_e2e",
        group_name="execution.egress",
        nodes=["format_output", "egress_line_bridge"],
    )
    inputs = [
        BoundaryDispatchInput(
            payload=WindowedDecision(
                line_no=i,
                id=str(i),
                customer_id="10",
                accepted=(i % 2 == 0),
            ),
            dispatch_group="execution.egress",
            target="format_output",
            tombstone=(i == 3),
        )
        for i in range(1, 4)
    ]

    outputs = execute_child_boundary_loop_from_bundle(bundle=bundle, inputs=inputs)
    sink_lines = _envelopes_with_payload_type(outputs, SinkLine)

    assert len(sink_lines) == 3
    assert [json.loads(item.payload.text)["id"] for item in sink_lines] == ["1", "2", "3"]
    assert [json.loads(item.payload.text)["accepted"] for item in sink_lines] == [False, True, False]
    assert sink_lines[-1].tombstone is True
    assert all(item.tombstone is False for item in sink_lines[:-1])


def test_collective_e2e_ingress_features_policy_egress_preserves_exact_record_count(tmp_path) -> None:
    input_path = tmp_path / "input.txt"
    line_count = 40
    input_path.write_text(
        "\n".join(_input_line(i) for i in range(1, line_count + 1)) + "\n",
        encoding="utf-8",
    )

    ingress_bundle = _bundle_for_group(
        scenario_id="fund_load_collective_ingress",
        group_name="execution.ingress",
        nodes=["source:source", "ingress_line_bridge", "parse_load_attempt"],
        adapters={
            "source": {
                "emit_tombstone": True,
                "settings": {
                    "path": str(input_path),
                    "format": "text/jsonl",
                    "encoding": "utf-8",
                    "decode_errors": "strict",
                },
                "binds": ["stream"],
            }
        },
    )
    ingress_outputs = execute_child_boundary_loop_from_bundle(
        bundle=ingress_bundle,
        inputs=[
            BoundaryDispatchInput(
                payload=BootstrapControl(target="source:source"),
                dispatch_group="execution.ingress",
                target="source:source",
            )
        ],
    )
    attempts = _envelopes_with_payload_type(ingress_outputs, LoadAttempt)
    assert len(attempts) == line_count
    assert attempts[-1].tombstone is True

    features_bundle = _bundle_for_group(
        scenario_id="fund_load_collective_features",
        group_name="execution.features",
        nodes=["compute_time_keys", "idempotency_gate", "compute_features"],
    )
    features_outputs = execute_child_boundary_loop_from_bundle(
        bundle=features_bundle,
        inputs=_to_boundary_inputs(
            envelopes=attempts,
            dispatch_group="execution.features",
            target="compute_time_keys",
        ),
    )
    enriched = _envelopes_with_payload_type(features_outputs, EnrichedAttempt)
    assert len(enriched) == line_count
    assert enriched[-1].tombstone is True

    policy_bundle = _bundle_for_group(
        scenario_id="fund_load_collective_policy",
        group_name="execution.policy",
        nodes=["evaluate_policies", "update_windows"],
    )
    policy_outputs = execute_child_boundary_loop_from_bundle(
        bundle=policy_bundle,
        inputs=_to_boundary_inputs(
            envelopes=enriched,
            dispatch_group="execution.policy",
            target="evaluate_policies",
        ),
    )
    decisions = _envelopes_with_payload_type(policy_outputs, WindowedDecision)
    assert len(decisions) == line_count
    assert decisions[-1].tombstone is True

    egress_bundle = _bundle_for_group(
        scenario_id="fund_load_collective_egress",
        group_name="execution.egress",
        nodes=["format_output", "egress_line_bridge"],
    )
    egress_outputs = execute_child_boundary_loop_from_bundle(
        bundle=egress_bundle,
        inputs=_to_boundary_inputs(
            envelopes=decisions,
            dispatch_group="execution.egress",
            target="format_output",
        ),
    )
    sink_lines = _envelopes_with_payload_type(egress_outputs, SinkLine)
    assert len(sink_lines) == line_count
    output_ids = [json.loads(item.payload.text)["id"] for item in sink_lines]
    assert output_ids == [str(i) for i in range(1, line_count + 1)]
    assert sink_lines[-1].tombstone is True
