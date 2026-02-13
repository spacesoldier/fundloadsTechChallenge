from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stream_kernel.execution.transport.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.child_bootstrap import (
    ChildBootstrapBundle,
    ChildRuntimeBootstrapError,
    execute_child_boundary_loop_from_bundle,
    bootstrap_child_runtime_from_bundle,
)
from stream_kernel.execution.orchestration.lifecycle_orchestration import BoundaryDispatchInput
from stream_kernel.platform.services.reply_waiter import TerminalEvent
from stream_kernel.platform.services.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.transport import RuntimeTransportService, TcpLocalRuntimeTransportService


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
                "from stream_kernel.platform.services.reply_waiter import TerminalEvent",
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
