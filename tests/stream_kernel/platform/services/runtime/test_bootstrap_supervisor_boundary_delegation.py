from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from stream_kernel.execution.orchestration.child_bootstrap import build_child_bootstrap_bundle
from stream_kernel.execution.orchestration.lifecycle_orchestration import BoundaryDispatchInput
from stream_kernel.execution.transport.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.platform.services.runtime.bootstrap import MultiprocessBootstrapSupervisor
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.routing.envelope import Envelope


def _runtime_tcp_local_generated() -> dict[str, object]:
    return {
        "strict": True,
        "discovery_modules": [],
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


def test_p5pre_exec_01_boundary_is_executed_in_target_worker_process(tmp_path: Path) -> None:
    # P5PRE-EXEC-01: process supervisor should execute boundary batch in target worker and return terminal outputs.
    package_name = "child_pkg_exec_step_e"
    pkg = tmp_path / package_name
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "import os",
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent",
                "",
                "@node(name='child.echo', consumes=[], emits=[])",
                "def child_echo(payload, ctx):",
                "    _ = ctx",
                "    return [TerminalEvent(status='success', payload={'echo': payload, 'worker_pid': os.getpid()})]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = [package_name]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"e" * n, now_fn=lambda: 7)
    bundle = build_child_bootstrap_bundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=[package_name],
        runtime=runtime,
        key_bundle=key_bundle,
    )
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups(
        [
            {"name": "web", "workers": 1},
            {"name": "execution.cpu", "workers": 1},
        ]
    )
    supervisor.load_child_bootstrap_bundle(bundle)

    sys.path.insert(0, str(tmp_path))
    try:
        supervisor.start_groups(["web", "execution.cpu"])
        assert supervisor.wait_ready(2) is True

        result = supervisor.execute_boundary(
            run=lambda: None,
            run_id="run",
            scenario_id="scenario",
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 1},
                    dispatch_group="execution.cpu",
                    target="child.echo",
                    trace_id="t1",
                    reply_to="http:req-1",
                )
            ],
        )
        assert result.local_deliveries == []
        assert result.boundary_deliveries == []
        assert len(result.terminal_outputs) == 1
        output = result.terminal_outputs[0]
        assert isinstance(output, Envelope)
        assert output.trace_id == "t1"
        assert output.reply_to == "http:req-1"
        assert isinstance(output.payload, TerminalEvent)
        assert output.payload.status == "success"
        assert isinstance(output.payload.payload, dict)
        assert output.payload.payload.get("echo") == {"v": 1}
        assert output.payload.payload["worker_pid"] != os.getpid()
    finally:
        sys.path.remove(str(tmp_path))
        try:
            supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
        except Exception:  # noqa: BLE001 - keep cleanup resilient in constrained environments.
            supervisor.force_terminate_groups(["web", "execution.cpu"])


def test_p5pre_exec_02_unknown_dispatch_group_is_transport_error() -> None:
    # P5PRE-EXEC-02: unknown dispatch group must fail deterministically as transport/boundary-unavailable category.
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups([{"name": "execution.cpu", "workers": 1}])
    try:
        supervisor.start_groups(["execution.cpu"])
        assert supervisor.wait_ready(2) is True
        with pytest.raises(ConnectionError, match="execution.gpu"):
            supervisor.execute_boundary(
                run=lambda: None,
                run_id="run",
                scenario_id="scenario",
                inputs=[
                    BoundaryDispatchInput(
                        payload={"v": 1},
                        dispatch_group="execution.gpu",
                        target="child.echo",
                        trace_id="t1",
                    )
                ],
            )
    finally:
        try:
            supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
        except Exception:  # noqa: BLE001 - keep cleanup resilient in constrained environments.
            supervisor.force_terminate_groups(["execution.cpu"])


def test_p5pre_exec_03_child_execution_error_details_are_propagated(tmp_path: Path) -> None:
    # P5PRE-EXEC-03: supervisor error should include child execution failure details for diagnostics.
    package_name = "child_pkg_exec_step_e_fail"
    pkg = tmp_path / package_name
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@node(name='child.fail', consumes=[], emits=[])",
                "def child_fail(payload, ctx):",
                "    _ = (payload, ctx)",
                "    raise ValueError('boom:child.fail')",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated()
    runtime["discovery_modules"] = [package_name]
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"e" * n, now_fn=lambda: 7)
    bundle = build_child_bootstrap_bundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=[package_name],
        runtime=runtime,
        key_bundle=key_bundle,
    )
    supervisor = MultiprocessBootstrapSupervisor()
    supervisor.configure_process_groups([{"name": "execution.cpu", "workers": 1, "nodes": ["child.fail"]}])
    supervisor.load_child_bootstrap_bundle(bundle)

    sys.path.insert(0, str(tmp_path))
    try:
        supervisor.start_groups(["execution.cpu"])
        assert supervisor.wait_ready(2) is True
        with pytest.raises(RuntimeError, match="boom:child.fail"):
            supervisor.execute_boundary(
                run=lambda: None,
                run_id="run",
                scenario_id="scenario",
                inputs=[
                    BoundaryDispatchInput(
                        payload={"v": 1},
                        dispatch_group="execution.cpu",
                        target="child.fail",
                        trace_id="t1",
                    )
                ],
            )
    finally:
        sys.path.remove(str(tmp_path))
        try:
            supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
        except Exception:  # noqa: BLE001 - keep cleanup resilient in constrained environments.
            supervisor.force_terminate_groups(["execution.cpu"])
