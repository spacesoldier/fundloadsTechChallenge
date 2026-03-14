from __future__ import annotations

import json

from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime import (
    execute_child_boundary_loop_from_bundle,
    execute_child_boundary_loop_with_runtime,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
    ChildBootstrapBundle,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service import (
    DefaultLeafRuntimeBootstrapService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from fund_load.domain.messages import LoadAttempt


def _runtime_tcp_local_generated() -> dict[str, object]:
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
                    "name": "execution.ingress",
                    "workers": 1,
                    "nodes": [
                        "source:source",
                        "ingress_line_bridge",
                        "parse_load_attempt",
                    ],
                }
            ],
        },
    }


def _input_line(idx: int) -> str:
    return json.dumps(
        {
            "id": str(idx),
            "customer_id": "10",
            "load_amount": f"${idx}.00",
            "time": f"2025-01-01T00:00:{idx:02d}Z",
        }
    )


def _bundle_for_input(*, input_path: str) -> ChildBootstrapBundle:
    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"t" * n, now_fn=lambda: 17)
    return ChildBootstrapBundle(
        scenario_id="fund_load_ingress_e2e",
        process_group="execution.ingress",
        discovery_modules=["fund_load"],
        runtime=runtime,
        adapters={
            "source": {
                "emit_tombstone": True,
                "settings": {
                    "path": input_path,
                    "format": "text/jsonl",
                    "encoding": "utf-8",
                    "decode_errors": "strict",
                },
                "binds": ["stream"],
            },
        },
        key_bundle=key_bundle,
    )


def _load_attempts(outputs: list[object]) -> list[object]:
    return [item for item in outputs if isinstance(getattr(item, "payload", None), LoadAttempt)]


def test_ingress_group_e2e_releases_exactly_input_records_and_final_tombstone(tmp_path) -> None:
    input_path = tmp_path / "input.txt"
    lines = [_input_line(i) for i in range(1, 6)]
    input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bundle = _bundle_for_input(input_path=str(input_path))

    outputs = execute_child_boundary_loop_from_bundle(
        bundle=bundle,
        inputs=[
            BoundaryDispatchInput(
                payload=BootstrapControl(target="source:source"),
                dispatch_group="execution.ingress",
                target="source:source",
            )
        ],
    )

    attempts = _load_attempts(outputs)
    assert len(attempts) == len(lines)
    assert [item.payload.id for item in attempts] == [str(i) for i in range(1, 6)]
    assert [item.payload.line_no for item in attempts] == [1, 2, 3, 4, 5]
    assert attempts[-1].tombstone is True
    assert all(item.tombstone is False for item in attempts[:-1])


def test_ingress_group_e2e_under_root_like_single_shot_control_preserves_exact_output(tmp_path) -> None:
    input_path = tmp_path / "input.txt"
    lines = [_input_line(i) for i in range(1, 8)]
    input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bundle = _bundle_for_input(input_path=str(input_path))
    child = DefaultLeafRuntimeBootstrapService().bootstrap_runtime(bundle=bundle)

    collected: list[object] = []
    max_steps = len(lines) + 4
    for _ in range(max_steps):
        outputs = execute_child_boundary_loop_with_runtime(
            child=child,
            inputs=[
                BoundaryDispatchInput(
                    payload=BootstrapControl(target="source:source", single_shot=True),
                    dispatch_group="execution.ingress",
                    target="source:source",
                )
            ],
        )
        attempts = _load_attempts(outputs)
        if attempts:
            collected.extend(attempts)
            if attempts[-1].tombstone:
                break

    assert len(collected) == len(lines)
    assert [item.payload.id for item in collected] == [str(i) for i in range(1, 8)]
    assert [item.payload.line_no for item in collected] == [1, 2, 3, 4, 5, 6, 7]
    assert collected[-1].tombstone is True
    assert all(item.tombstone is False for item in collected[:-1])
