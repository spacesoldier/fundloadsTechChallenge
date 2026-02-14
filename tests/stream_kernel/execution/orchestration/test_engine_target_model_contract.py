from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys

import pytest

from stream_kernel.execution.transport.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.orchestration.child_bootstrap import (
    ChildBootstrapBundle,
    bootstrap_child_runtime_from_bundle,
    execute_child_boundary_loop,
    execute_child_boundary_loop_from_bundle,
)
from stream_kernel.execution.orchestration.lifecycle_orchestration import (
    BoundaryDispatchInput,
    _build_boundary_dispatch_inputs,
)
from stream_kernel.execution.runtime.runner import SyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ReplyAwareObservabilityService,
)
from stream_kernel.platform.services.messaging.reply_coordinator import legacy_reply_coordinator
from stream_kernel.platform.services.messaging.reply_waiter import InMemoryReplyWaiterService, TerminalEvent
from stream_kernel.routing.envelope import Envelope


def _runtime_tcp_local_generated(*, discovery_modules: list[str]) -> dict[str, object]:
    return {
        "strict": True,
        "discovery_modules": discovery_modules,
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


def test_engine_target_model_runner_does_not_own_reply_waiter_contract() -> None:
    # ENG-REPLY-01 (RED): runner should not expose direct waiter fields in the target model.
    runner_fields = {f.name for f in fields(SyncRunner)}
    assert "reply_coordinator" not in runner_fields
    assert "reply_waiter" not in runner_fields
    assert "reply_timeout_seconds" not in runner_fields


def test_engine_target_model_terminal_path_does_not_require_waiter_in_runner() -> None:
    # ENG-REPLY-02 (RED): terminal handling should not require reply waiter inside runner hot path.
    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [TerminalEvent(status="success", payload={"ok": True})]

    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=InMemoryQueue(),
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
    )
    runner.work_queue.push(Envelope(payload={"v": 1}, target="A", trace_id="t1"))  # type: ignore[union-attr]

    # Target contract: runner succeeds without direct waiter dependency.
    runner.run()


def test_engine_target_model_reply_to_without_target_registers_once() -> None:
    # ENG-REPLY-03 (RED): ingress correlation should be registered once even when initial envelope has no target.
    reply_waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [TerminalEvent(status="success", payload={"done": True})]

    registry = InMemoryConsumerRegistry()
    registry.register(int, ["A"])

    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=InMemoryQueue(),
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=ReplyAwareObservabilityService(
            inner=NoOpObservabilityService(),
            reply_coordinator=legacy_reply_coordinator(reply_waiter=reply_waiter),
        ),
    )

    runner.run_inputs(
        [Envelope(payload=7, reply_to="http:req-1")],
        run_id="run",
        scenario_id="scenario",
    )

    counters = reply_waiter.diagnostics_counters()
    assert counters["registered"] == 1
    assert counters["completed"] == 1
    assert counters["late_reply_drop"] == 0


def test_engine_target_model_router_returns_structured_result() -> None:
    # ENG-ROUTE-01 (RED): router should return structured routing result, not raw tuple list.
    router = RoutingService(registry=InMemoryConsumerRegistry({int: ["A"]}), strict=True)
    result = router.route([1])
    assert not isinstance(result, list)
    assert hasattr(result, "local_deliveries")
    assert hasattr(result, "boundary_deliveries")
    assert hasattr(result, "terminal_outputs")


def test_engine_target_model_boundary_dispatch_is_per_target_group() -> None:
    # ENG-PLACEMENT-01 (RED): boundary dispatch should use per-target placement, not one global group.
    runtime = {
        "platform": {
            "process_groups": [
                {"name": "web"},
                {"name": "execution.cpu", "nodes": ["node.cpu"]},
                {"name": "execution.asyncio", "nodes": ["node.async"]},
            ]
        }
    }
    inputs = [
        Envelope(payload={"v": 1}, target="node.cpu", trace_id="t1"),
        Envelope(payload={"v": 2}, target="node.async", trace_id="t2"),
    ]

    boundary_inputs, _dispatch_group, _aliases = _build_boundary_dispatch_inputs(
        runtime=runtime,
        inputs=inputs,
    )
    assert len(boundary_inputs) == 2
    assert {item.dispatch_group for item in boundary_inputs} == {"execution.cpu", "execution.asyncio"}


def test_engine_target_model_boundary_dispatch_fails_on_missing_target_placement() -> None:
    # ENG-PLACEMENT-03: explicit placement mode should fail when target has no process-group mapping.
    runtime = {
        "platform": {
            "process_groups": [
                {"name": "web"},
                {"name": "execution.cpu", "nodes": ["node.cpu"]},
                {"name": "execution.asyncio", "nodes": ["node.async"]},
            ]
        }
    }
    inputs = [Envelope(payload={"v": 1}, target="node.unknown", trace_id="t1")]

    with pytest.raises(ValueError, match="Missing process-group placement for target 'node.unknown'"):
        _build_boundary_dispatch_inputs(runtime=runtime, inputs=inputs)


def test_engine_target_model_child_loop_uses_di_invocation_parity(tmp_path: Path) -> None:
    # ENG-CHILD-01 (RED): child invocation should resolve injected service dependencies like parent flow.
    pkg = tmp_path / "child_pkg_engine_target"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.application_context.inject import inject",
                "from stream_kernel.application_context.service import service",
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@service(name='child_probe_service')",
                "class ProbeService:",
                "    def ping(self, payload):",
                "        return {'ok': True, 'payload': payload}",
                "",
                "@node(name='child.need_service', consumes=[], emits=[])",
                "class NeedsService:",
                "    probe = inject.service(ProbeService)",
                "",
                "    def __call__(self, payload, ctx):",
                "        _ = ctx",
                "        return [self.probe.ping(payload)]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated(discovery_modules=["child_pkg_engine_target"])
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"s" * n, now_fn=lambda: 10)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_engine_target"],
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
                    target="child.need_service",
                    trace_id="t1",
                )
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert len(outputs) == 1
    assert outputs[0].payload == {"ok": True, "payload": {"v": 7}}


def test_engine_target_model_child_loop_uses_context_metadata_parity(tmp_path: Path) -> None:
    # ENG-CHILD-02: child invocation should use the same metadata visibility rules as parent runner.
    pkg = tmp_path / "child_pkg_engine_ctx"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "",
                "@node(name='child.ctx_regular', consumes=[], emits=[])",
                "def child_ctx_regular(payload, ctx):",
                "    _ = payload",
                "    return [{'kind': 'regular', 'has_private': '__trace_id' in ctx, 'keys': sorted(ctx.keys())}]",
                "",
                "@node(name='child.ctx_service', consumes=[], emits=[], service=True)",
                "def child_ctx_service(payload, ctx):",
                "    _ = payload",
                "    return [{'kind': 'service', 'has_private': '__trace_id' in ctx, 'trace': ctx.get('__trace_id')}]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated(discovery_modules=["child_pkg_engine_ctx"])
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"q" * n, now_fn=lambda: 11)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_engine_ctx"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        child = bootstrap_child_runtime_from_bundle(bundle)
        context_service = child.scenario_scope.resolve("service", ContextService)
        assert isinstance(context_service, ContextService)
        context_service.seed(
            trace_id="t-meta-1",
            payload={"v": 1},
            run_id="run",
            scenario_id="scenario_child",
        )
        outputs = execute_child_boundary_loop(
            child=child,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 1},
                    dispatch_group="execution.cpu",
                    target="child.ctx_regular",
                    trace_id="t-meta-1",
                ),
                BoundaryDispatchInput(
                    payload={"v": 1},
                    dispatch_group="execution.cpu",
                    target="child.ctx_service",
                    trace_id="t-meta-1",
                ),
            ],
        )
    finally:
        sys.path.remove(str(tmp_path))

    payloads = [item.payload for item in outputs]
    assert {"kind": "regular", "has_private": False, "keys": []} in payloads
    assert {"kind": "service", "has_private": True, "trace": "t-meta-1"} in payloads


def test_engine_target_model_child_loop_emits_observability_callbacks(tmp_path: Path) -> None:
    # ENG-CHILD-03: child invocation should emit observability lifecycle callbacks.
    pkg = tmp_path / "child_pkg_engine_obs"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.application_context.service import service",
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.platform.services.observability import ObservabilityService",
                "",
                "@service(name='child_test_observability')",
                "class ChildTestObservability(ObservabilityService):",
                "    events = []",
                "    def before_node(self, *, node_name, payload, ctx, trace_id):",
                "        self.events.append(('before', node_name, trace_id))",
                "        return 'state'",
                "    def after_node(self, *, node_name, payload, ctx, trace_id, outputs, state):",
                "        self.events.append(('after', node_name, trace_id, len(outputs), state))",
                "    def on_node_error(self, *, node_name, payload, ctx, trace_id, error, state):",
                "        self.events.append(('error', node_name, trace_id, state))",
                "    def on_run_end(self):",
                "        self.events.append(('run_end',))",
                "",
                "@node(name='child.obs_probe', consumes=[], emits=[])",
                "def child_obs_probe(payload, ctx):",
                "    _ = ctx",
                "    return [{'ok': payload}]",
                "",
            ]
        ),
    )

    runtime = _runtime_tcp_local_generated(discovery_modules=["child_pkg_engine_obs"])
    key_bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"w" * n, now_fn=lambda: 12)
    bundle = ChildBootstrapBundle(
        scenario_id="scenario_child",
        process_group="execution.cpu",
        discovery_modules=["child_pkg_engine_obs"],
        runtime=runtime,
        key_bundle=key_bundle,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        import child_pkg_engine_obs.nodes as obs_nodes

        obs_nodes.ChildTestObservability.events.clear()
        child = bootstrap_child_runtime_from_bundle(bundle)
        _ = execute_child_boundary_loop(
            child=child,
            inputs=[
                BoundaryDispatchInput(
                    payload={"v": 3},
                    dispatch_group="execution.cpu",
                    target="child.obs_probe",
                    trace_id="t-obs-1",
                )
            ],
        )
        events = list(obs_nodes.ChildTestObservability.events)
    finally:
        sys.path.remove(str(tmp_path))

    assert ("before", "child.obs_probe", "t-obs-1") in events
    assert ("after", "child.obs_probe", "t-obs-1", 1, "state") in events
    assert ("run_end",) in events
