from __future__ import annotations

import sys
from pathlib import Path
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fund_load.domain.messages import LoadAttempt
from fund_load.domain.money import Money
from fund_load.usecases.messages import AttemptWithKeys
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
    ChildBootstrapBundle,
    ChildRuntimeBootstrapError,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime import (
    execute_child_boundary_loop_from_bundle,
    execute_child_boundary_loop_with_runtime,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service import (
    DefaultLeafRuntimeBootstrapService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle import BoundaryDispatchInput
from stream_kernel.integration.consumer_registry import ConsumerRegistry
from stream_kernel.adapters.file_io import SinkLine
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.observability import (
    ObservabilityPipelineService,
    ObservabilityService,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.transport import RuntimeTransportService, TcpLocalRuntimeTransportService


_leaf_runtime_bootstrap_service = DefaultLeafRuntimeBootstrapService()


def bootstrap_child_runtime_from_bundle(bundle: object):
    return _leaf_runtime_bootstrap_service.bootstrap_runtime(bundle=bundle)


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
    assert child.runner_profile_effective in {"sync", "async"}
    assert isinstance(child.runner_profile_nodes, dict)
    assert isinstance(child.async_service_contracts, list)
    assert isinstance(child.async_adapter_bindings, list)


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


def test_child_boundary_loop_executes_async_node_outputs(tmp_path: Path) -> None:
    # HANDOFF-C-01A: async node output must be awaited in child boundary loop.
    pkg = tmp_path / "child_pkg_async_node"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent",
                "",
                "@node(name='child.async_echo', consumes=[], emits=[])",
                "async def child_async_echo(payload, ctx):",
                "    _ = ctx",
                "    return [TerminalEvent(status='success', payload={'echo': payload})]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_async_node"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"a" * n, now_fn=lambda: 41)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_async_node"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        outputs = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 9},
                    dispatch_group="execution.cpu",
                    target="child.async_echo",
                    trace_id="t-async",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].trace_id == "t-async"
    assert outputs[0].payload == TerminalEvent(status="success", payload={"echo": {"v": 9}})


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


def test_child_bootstrap_marks_source_and_sink_runtime_nodes_async(tmp_path: Path) -> None:
    # HANDOFF-C-04A: source/sink runtime wrappers must inherit async adapter execution capability.
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')
    output_path = tmp_path / "output.txt"
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"s" * n, now_fn=lambda: 42)
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

    child = bootstrap_child_runtime_from_bundle(bundle)
    assert child.runner_profile_nodes.get("source:source") == "async"
    assert child.runner_profile_nodes.get("sink:sink") == "async"


def test_child_bootstrap_runner_profile_is_scoped_to_current_process_group(tmp_path: Path) -> None:
    # HANDOFF-C-04B: worker runner profile must be inferred from nodes assigned to current process group.
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')
    output_path = tmp_path / "output.txt"
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    runtime["platform"]["process_groups"] = [
        {
            "name": "execution.ingress",
            "nodes": ["source:source", "ingress_line_bridge", "parse_load_attempt"],
        },
        {
            "name": "execution.features",
            "nodes": ["compute_time_keys", "idempotency_gate", "compute_features"],
        },
        {
            "name": "execution.egress",
            "nodes": ["format_output", "egress_line_bridge", "sink:sink"],
        },
    ]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"g" * n, now_fn=lambda: 43)
    bundle = ChildBootstrapBundle(
        scenario_id="baseline_mp",
        process_group="execution.features",
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
    assert set(child.runner_profile_nodes.keys()) == {
        "compute_time_keys",
        "idempotency_gate",
        "compute_features",
    }
    assert child.runner_profile_effective == "async"


def test_child_bootstrap_runner_profile_respects_explicit_sync_request(tmp_path: Path) -> None:
    input_path = tmp_path / "input.txt"
    input_path.write_text('{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n')
    output_path = tmp_path / "output.txt"
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["fund_load"]
    runtime["__runner_profile_requested"] = "sync"
    runtime["platform"]["process_groups"] = [
        {
            "name": "execution.features",
            "nodes": ["compute_time_keys", "idempotency_gate", "compute_features"],
        }
    ]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"g" * n, now_fn=lambda: 43)
    bundle = ChildBootstrapBundle(
        scenario_id="baseline_mp",
        process_group="execution.features",
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
    assert child.runner_profile_effective == "sync"


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


def test_child_boundary_loop_worker_transport_only_emits_trace_dispatch_envelope(tmp_path: Path) -> None:
    pkg = tmp_path / "child_pkg_obs_worker_transport"
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

    runtime = _runtime_tcp_local_generated()
    runtime["__process_role"] = "worker"
    runtime["discovery_modules"] = ["child_pkg_obs_worker_transport"]
    runtime["platform"]["bootstrap"] = {"mode": "process_supervisor"}
    runtime["platform"]["process_groups"] = [
        {"name": "execution.ingress", "nodes": ["child.echo"]},
        {"name": "system.observability", "nodes": ["system.obs.trace_dispatch"]},
    ]
    runtime["observability"] = {
        "service_process": {"enabled": True, "group_name": "system.observability"},
        "tracing": {"exporters": [{"kind": "otel_otlp", "settings": {"endpoint": "http://collector:4318/v1/traces"}}]},
    }
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"w" * n, now_fn=lambda: 49)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.ingress",
        discovery_modules=["child_pkg_obs_worker_transport"],
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
                    dispatch_group="execution.ingress",
                    target="child.echo",
                    trace_id="t-worker",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert any(
        isinstance(getattr(item, "payload", None), TraceDispatchEvent)
        and getattr(item, "target", None) == "system.obs.trace_dispatch"
        for item in outputs
    ), outputs


def test_child_boundary_loop_keeps_process_group_route_attrs_for_full_local_chain(tmp_path: Path) -> None:
    # CHILD-BOOT-07A: all spans in a local chain should keep process-group route attributes.
    pkg = tmp_path / "child_pkg_obs_chain"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@dataclass(frozen=True)",
                "class Token:",
                "    value: int",
                "",
                "@node(name='child.n1', consumes=[], emits=[Token])",
                "def child_n1(payload, ctx):",
                "    _ = ctx",
                "    return [Token(value=int(payload['v']))]",
                "",
                "@node(name='child.n2', consumes=[Token], emits=[])",
                "def child_n2(payload, ctx):",
                "    _ = ctx",
                "    return [f\"ok:{payload.value}\"]",
                "",
            ]
        ),
    )

    exported: list[dict[str, object]] = []
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_obs_chain"]
    runtime["platform"]["process_groups"] = [
        {"name": "execution.ingress", "nodes": ["child.n1", "child.n2"]},
    ]
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
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"z" * n, now_fn=lambda: 47)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.ingress",
        discovery_modules=["child_pkg_obs_chain"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        _ = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 9},
                    dispatch_group="execution.ingress",
                    target="child.n1",
                    trace_id="t-chain",
                    source_group="supervisor.entry",
                    route_hop=0,
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert [span.get("name") for span in exported] == ["child.n1", "child.n2"]
    for span in exported:
        attrs = span.get("attributes", {})
        assert isinstance(attrs, dict)
        assert attrs.get("process_group") == "execution.ingress"
        assert attrs.get("handoff_from") == "supervisor.entry"
        assert attrs.get("route_hop") == 0


def test_bootstrap_child_runtime_worker_planning_keeps_only_business_nodes(tmp_path: Path) -> None:
    # OBS-CHILD-PLAN-01: worker planning should keep business nodes only in primitive multiprocess mode.
    pkg = tmp_path / "child_pkg_obs_group_plan"
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
    runtime = _runtime_tcp_local_generated()
    runtime["__process_role"] = "worker"
    runtime["discovery_modules"] = ["child_pkg_obs_group_plan"]
    runtime["platform"]["process_groups"] = [
        {"name": "execution.ingress", "nodes": ["child.echo"]},
    ]
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "stdout"},
            ]
        }
    }
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"o" * n, now_fn=lambda: 48)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.ingress",
        discovery_modules=["child_pkg_obs_group_plan"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        child = bootstrap_child_runtime_from_bundle(bundle)
    finally:
        sys.path.remove(str(tmp_path))

    assert "child.echo" in child.runner_profile_nodes
    # Worker runtime is transport-only for observability dispatch.
    assert "system.obs.trace_dispatch" not in child.runner_profile_nodes


def test_child_boundary_loop_executes_local_chain_inside_group_before_emitting(tmp_path: Path) -> None:
    # RUN-UNI-C1: boundary child loop must execute local group chain via runner before emitting outputs.
    pkg = tmp_path / "child_pkg_phase_c_local_chain"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@dataclass(frozen=True)",
                "class Token:",
                "    value: int",
                "",
                "@node(name='child.n1', consumes=[], emits=[Token])",
                "def child_n1(payload, ctx):",
                "    _ = ctx",
                "    return [Token(value=int(payload['v']))]",
                "",
                "@node(name='child.n2', consumes=[Token], emits=[])",
                "def child_n2(payload, ctx):",
                "    _ = ctx",
                "    return [f\"done:{payload.value}\"]",
                "",
            ]
        ),
    )
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_phase_c_local_chain"]
    runtime["platform"]["process_groups"] = [
        {"name": "execution.cpu", "nodes": ["child.n1", "child.n2"]},
    ]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"r" * n, now_fn=lambda: 44)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_phase_c_local_chain"],
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
                    target="child.n1",
                    trace_id="t-phase-c-1",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].target is None
    assert outputs[0].trace_id == "t-phase-c-1"
    assert outputs[0].payload == "done:7"


def test_child_boundary_loop_preserves_trace_reply_and_span_through_local_chain(tmp_path: Path) -> None:
    # RUN-UNI-C2: trace/reply/span metadata should remain consistent after local chain execution.
    pkg = tmp_path / "child_pkg_phase_c_trace_reply"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.routing.envelope import Envelope",
                "",
                "@dataclass(frozen=True)",
                "class Token:",
                "    value: int",
                "",
                "@dataclass(frozen=True)",
                "class Done:",
                "    value: int",
                "",
                "@node(name='child.n1', consumes=[], emits=[Token])",
                "def child_n1(payload, ctx):",
                "    _ = ctx",
                "    return [Token(value=int(payload['v']))]",
                "",
                "@node(name='child.n2', consumes=[Token], emits=[Done])",
                "def child_n2(payload, ctx):",
                "    _ = ctx",
                "    return [Envelope(payload=Done(value=payload.value), span_id='node2-span')]",
                "",
                "@node(name='child.n3', consumes=[Done], emits=[])",
                "def child_n3(payload, ctx):",
                "    _ = (payload, ctx)",
                "    return []",
                "",
            ]
        ),
    )
    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_phase_c_trace_reply"]
    runtime["platform"]["process_groups"] = [
        {"name": "execution.cpu", "nodes": ["child.n1", "child.n2"]},
        {"name": "execution.remote", "nodes": ["child.n3"]},
    ]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"q" * n, now_fn=lambda: 45)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_phase_c_trace_reply"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        outputs = execute_child_boundary_loop_from_bundle(
            bundle=bundle,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 5},
                    dispatch_group="execution.cpu",
                    target="child.n1",
                    trace_id="trace-c2",
                    reply_to="http:req-42",
                    span_id="parent-span",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].target == "child.n3"
    assert outputs[0].trace_id == "trace-c2"
    assert outputs[0].reply_to == "http:req-42"
    assert outputs[0].span_id == "node2-span"


def test_child_boundary_loop_observability_callbacks_fire_once_per_executed_node(tmp_path: Path) -> None:
    # RUN-UNI-C3: boundary execution should preserve once-per-node observability callbacks.
    pkg = tmp_path / "child_pkg_phase_c_observability"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@dataclass(frozen=True)",
                "class Token:",
                "    value: int",
                "",
                "@node(name='child.n1', consumes=[], emits=[Token])",
                "def child_n1(payload, ctx):",
                "    _ = ctx",
                "    return [Token(value=int(payload['v']))]",
                "",
                "@node(name='child.n2', consumes=[Token], emits=[])",
                "def child_n2(payload, ctx):",
                "    _ = ctx",
                "    return [f\"ok:{payload.value}\"]",
                "",
            ]
        ),
    )

    class _CountingObservability:
        def __init__(self) -> None:
            self.before_nodes: list[str] = []
            self.after_nodes: list[str] = []
            self.errors = 0
            self.run_end = 0

        def before_node(self, *, node_name: str, **_: object) -> object | None:
            self.before_nodes.append(node_name)
            return None

        def after_node(self, *, node_name: str, outputs: list[object], **_: object) -> object | None:
            _ = outputs
            self.after_nodes.append(node_name)
            return None

        def on_node_error(self, **_: object) -> object | None:
            self.errors += 1
            return None

        def on_run_end(self) -> None:
            self.run_end += 1

        def on_ingress(self, **_: object) -> object | None:
            return None

        def on_terminal_event(self, **_: object) -> object | None:
            return None

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = ["child_pkg_phase_c_observability"]
    runtime["platform"]["process_groups"] = [
        {"name": "execution.cpu", "nodes": ["child.n1", "child.n2"]},
    ]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"p" * n, now_fn=lambda: 46)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_phase_c_observability"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        child = bootstrap_child_runtime_from_bundle(bundle)
    finally:
        sys.path.remove(str(tmp_path))

    counting = _CountingObservability()
    child.scenario_scope._instances[("service", ObservabilityService, None)] = counting  # type: ignore[attr-defined]
    child.scenario_scope._instances[("service", ObservabilityPipelineService, None)] = counting  # type: ignore[attr-defined]

    outputs = execute_child_boundary_loop_with_runtime(
        child=child,
        inputs=[
            BoundaryDispatchInput(
                payload={"v": 3},
                dispatch_group="execution.cpu",
                target="child.n1",
                trace_id="trace-c3",
            )
        ],
    )

    assert outputs
    assert counting.before_nodes == ["child.n1", "child.n2"]
    assert counting.after_nodes == ["child.n1", "child.n2"]
    assert counting.errors == 0
    assert counting.run_end == 1
