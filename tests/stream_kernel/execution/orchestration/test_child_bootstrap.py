from __future__ import annotations

import sys
from pathlib import Path
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fund_load.domain.messages import LoadAttempt
from fund_load.domain.money import Money
from fund_load.usecases.messages import AttemptWithKeys
from stream_kernel.execution.transport.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.child_bootstrap import (
    ChildBootstrapBundle,
    ChildRuntimeBootstrapError,
    execute_child_boundary_loop_from_bundle,
    execute_child_boundary_loop_with_runtime,
    bootstrap_child_runtime_from_bundle,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle_orchestration import BoundaryDispatchInput
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.adapters.file_io import SinkLine
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService, TcpLocalRuntimeTransportService


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
            }
        },
    }


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_bootstrap_child_runtime_builds_discovery_and_di_from_bundle_metadata() -> None:
    # CHILD-BOOT-01: child runtime must bootstrap discovery and DI from metadata bundle.
    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"s" * n, now_fn=lambda: 1)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["fund_load"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    child = bootstrap_child_runtime_from_bundle(bundle)
    assert child.scenario_id == "scenario_child"
    assert child.process_group == "execution.cpu"
    assert child.modules
    assert child.injection_registry is not None
    assert child.scenario_scope is not None


def test_bootstrap_child_runtime_resolves_transport_and_lifecycle_from_di() -> None:
    # CHILD-BOOT-02: child bootstrap should resolve runtime transport and lifecycle services via DI.
    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"k" * n, now_fn=lambda: 2)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["fund_load"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    child = bootstrap_child_runtime_from_bundle(bundle)
    assert isinstance(child.runtime_transport, RuntimeTransportService)
    assert isinstance(child.runtime_transport, TcpLocalRuntimeTransportService)
    assert child.runtime_transport.transport.config.secret == key_bundle.execution_ipc.signing_secret
    assert isinstance(child.runtime_lifecycle, RuntimeLifecycleManager)


def test_bootstrap_child_runtime_rejects_malformed_bundle_deterministically() -> None:
    # CHILD-BOOT-03: malformed bundle should fail with deterministic child-bootstrap error.
    runtime = _runtime_tcp_local_generated()
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"m" * n, now_fn=lambda: 3)

    with pytest.raises(ChildRuntimeBootstrapError, match="discovery_modules"):
        bootstrap_child_runtime_from_bundle(
            ChildBootstrapBundle(
                scenario_id="scenario_child",
                process_group="execution.cpu",
                discovery_modules=["fund_load", ""],
                runtime=runtime,
                key_bundle=key_bundle,
            )
        )

    with pytest.raises(ChildRuntimeBootstrapError, match="key_bundle"):
        bootstrap_child_runtime_from_bundle(
            ChildBootstrapBundle(
                scenario_id="scenario_child",
                process_group="execution.cpu",
                discovery_modules=["fund_load"],
                runtime=runtime,
                key_bundle=None,  # type: ignore[arg-type]
            )
        )


def test_child_boundary_loop_executes_discovered_node_and_emits_terminal_envelope(
    tmp_path: Path,
) -> None:
    # HANDOFF-C-01: child loop should execute discovered node and emit terminal with ingress correlation metadata.
    pkg = tmp_path / "child_pkg_step_c"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent",
                "",
                "@node(name='child.echo', consumes=[], emits=[])",
                "def child_echo(payload, ctx):",
                "    _ = ctx",
                "    return [TerminalEvent(status='success', payload={'echo': payload})]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_step_c"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"c" * n, now_fn=lambda: 4)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_step_c"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        outputs = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 7},
                    dispatch_group="execution.cpu",
                    target="child.echo",
                    trace_id="t1",
                    reply_to="http:req-1",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].trace_id == "t1"
    assert outputs[0].reply_to == "http:req-1"
    assert outputs[0].payload == TerminalEvent(status="success", payload={"echo": {"v": 7}})


def test_child_boundary_loop_ignores_other_dispatch_groups(tmp_path: Path) -> None:
    # HANDOFF-C-02: child loop should execute only inputs for the selected dispatch_group.
    pkg = tmp_path / "child_pkg_group_filter"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@node(name='child.nop', consumes=[], emits=[])",
                "def child_nop(payload, ctx):",
                "    _ = (payload, ctx)",
                "    return ['ok']",
                "",
            ]
        ),
    )
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_group_filter"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"d" * n, now_fn=lambda: 5)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_group_filter"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        outputs = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 1},
                    dispatch_group="execution.cpu",
                    target="child.nop",
                    trace_id="t1",
                ),
                BoundaryDispatchInput(
                    payload={"v": 2},
                    dispatch_group="execution.asyncio",
                    target="child.nop",
                    trace_id="t2",
                ),
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].trace_id == "t1"
    assert outputs[0].payload == "ok"


def test_child_boundary_loop_unknown_target_is_deterministic_error(tmp_path: Path) -> None:
    # HANDOFF-C-03: unknown boundary target should fail with explicit child-bootstrap category.
    pkg = tmp_path / "child_pkg_unknown_target"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "nodes.py", "x = 1\n")

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_unknown_target"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"e" * n, now_fn=lambda: 6)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_unknown_target"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(ChildRuntimeBootstrapError, match="not discovered in child runtime"):
            execute_child_boundary_loop_from_bundle(
                bundle=bundle,
                inputs=[
                    BoundaryDispatchInput(
                        payload={"v": 1},
                        dispatch_group="execution.cpu",
                        target="child.unknown",
                        trace_id="t1",
                    )
                ],
            )
    finally:
        sys.path.remove(str(tmp_path))


def test_child_boundary_loop_executes_runtime_source_node_with_adapter_bundle(tmp_path: Path) -> None:
    # CHILD-BOOT-04: child runtime should materialize source:* wrappers from bundle.adapters and route outputs.
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')
    output_path = tmp_path / "output.txt"

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"f" * n, now_fn=lambda: 7)
    bundle = ChildBootstrapBundle(
        scenario_id="baseline_mp",
        process_group="execution.ingress",
        discovery_modules=["fund_load"],
        runtime=runtime,
        adapters={
            "source": {
                "settings": {
                    "path": str(input_path),
                    "format": "text/jsonl",
                    "encoding": "utf-8",
                    "decode_errors": "strict",
                },
                "binds": ["stream"],
            },
            "sink": {
                "settings": {"path": str(output_path), "format": "text/jsonl", "encoding": "utf-8"},
                "binds": ["stream"],
            },
        },
        key_bundle=key_bundle,
    )

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

    targets = [item.target for item in outputs if isinstance(item.target, str)]
    assert "ingress_line_bridge" in targets
    trace_ids = [item.trace_id for item in outputs if item.target == "ingress_line_bridge"]
    assert trace_ids and isinstance(trace_ids[0], str)
    assert trace_ids[0].startswith("run:source:")


def test_bootstrap_child_runtime_binds_runtime_consumer_registry_for_sink_wiring(tmp_path: Path) -> None:
    # CHILD-BOOT-08: runtime consumer registry bound in DI must include dynamic sink wiring.
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')
    output_path = tmp_path / "out.txt"

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"j" * n, now_fn=lambda: 11)
    bundle = ChildBootstrapBundle(
        scenario_id="baseline_mp",
        process_group="execution.egress",
        discovery_modules=["fund_load"],
        runtime=runtime,
        adapters={
            "source": {
                "settings": {
                    "path": str(input_path),
                    "format": "text/jsonl",
                    "encoding": "utf-8",
                    "decode_errors": "strict",
                },
                "binds": ["stream"],
            },
            "sink": {
                "settings": {"path": str(output_path), "format": "text/jsonl", "encoding": "utf-8"},
                "binds": ["stream"],
            },
        },
        key_bundle=key_bundle,
    )

    child = bootstrap_child_runtime_from_bundle(bundle)
    registry = child.scenario_scope.resolve("service", ConsumerRegistry)
    assert "sink:sink" in registry.get_consumers(SinkLine)


def test_child_boundary_loop_applies_node_config_from_bundle() -> None:
    # CHILD-BOOT-05: child bootstrap must apply config.nodes.* values when executing discovered nodes.
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"g" * n, now_fn=lambda: 8)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.features",
        discovery_modules=["fund_load"],
        runtime=runtime,
        config={"nodes": {"compute_time_keys": {"week_start": "SUN"}}},
        key_bundle=key_bundle,
    )

    outputs = execute_child_boundary_loop_from_bundle(
        bundle=bundle,
        inputs=[
            BoundaryDispatchInput(
                payload=LoadAttempt(
                    line_no=1,
                    id="1",
                    customer_id="10",
                    amount=Money(currency="USD", amount=Decimal("1.00")),
                    ts=datetime(2025, 1, 8, 0, 0, 0, tzinfo=UTC),
                ),
                dispatch_group="execution.features",
                target="compute_time_keys",
                trace_id="t1",
            )
        ],
    )

    assert len(outputs) == 1
    payload = outputs[0].payload
    assert isinstance(payload, AttemptWithKeys)
    assert payload.week_key.week_start == "SUN"


def test_child_boundary_runtime_reuse_preserves_source_state(tmp_path: Path) -> None:
    # CHILD-BOOT-06: reused child runtime must preserve source node state across boundary batches.
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"h" * n, now_fn=lambda: 9)
    bundle = ChildBootstrapBundle(
        scenario_id="baseline_mp",
        process_group="execution.ingress",
        discovery_modules=["fund_load"],
        runtime=runtime,
        adapters={
            "source": {
                "settings": {
                    "path": str(input_path),
                    "format": "text/jsonl",
                    "encoding": "utf-8",
                    "decode_errors": "strict",
                },
                "binds": ["stream"],
            }
        },
        key_bundle=key_bundle,
    )

    child = bootstrap_child_runtime_from_bundle(bundle)
    first = execute_child_boundary_loop_with_runtime(
        child=child,
        inputs=[
            BoundaryDispatchInput(
                payload=BootstrapControl(target="source:source"),
                dispatch_group="execution.ingress",
                target="source:source",
            )
        ],
    )
    second = execute_child_boundary_loop_with_runtime(
        child=child,
        inputs=[
            BoundaryDispatchInput(
                payload=BootstrapControl(target="source:source"),
                dispatch_group="execution.ingress",
                target="source:source",
            )
        ],
    )

    assert first
    assert second == []


def test_child_boundary_loop_emits_observability_traces_from_runtime_exporters(tmp_path: Path) -> None:
    # CHILD-BOOT-07: child runtime should emit tracing spans when runtime observability exporters are configured.
    pkg = tmp_path / "child_pkg_obs"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@node(name='child.echo', consumes=[], emits=[])",
                "def child_echo(payload, ctx):",
                "    _ = ctx",
                "    return [payload]",
                "",
            ]
        ),
    )

    exported: list[dict[str, object]] = []
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_obs"]
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "_export_fn": lambda span: exported.append(span),
                    },
                }
            ]
        }
    }
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"i" * n, now_fn=lambda: 10)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_obs"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        _ = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 7},
                    dispatch_group="execution.cpu",
                    target="child.echo",
                    trace_id="t1",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert exported
    assert exported[0].get("trace_id") == "t1"
