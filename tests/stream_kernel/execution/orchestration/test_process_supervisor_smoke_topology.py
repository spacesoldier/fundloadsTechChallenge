from __future__ import annotations

import sys
from pathlib import Path

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.builder import RuntimeBuildArtifacts, execute_runtime_artifacts
from stream_kernel.platform.services.bootstrap import BootstrapSupervisor, MultiprocessBootstrapSupervisor
from stream_kernel.platform.services.reply_coordinator import ReplyCoordinatorService, legacy_reply_coordinator
from stream_kernel.platform.services.reply_waiter import InMemoryReplyWaiterService, TerminalEvent
from stream_kernel.routing.envelope import Envelope


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _runtime_for_smoke(*, module_name: str) -> dict[str, object]:
    return {
        "discovery_modules": [module_name],
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
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [
                {"name": "execution.ingress", "workers": 1, "nodes": ["ingress.n1"]},
                {"name": "execution.features", "workers": 1, "nodes": ["features.n2"]},
                {"name": "execution.policy", "workers": 1, "nodes": ["policy.n3"]},
                {"name": "execution.egress", "workers": 1, "nodes": ["egress.n4"]},
            ],
        },
    }


def _artifacts_for_smoke(
    *,
    runtime: dict[str, object],
    supervisor: object,
    inputs: list[object],
    waiter: InMemoryReplyWaiterService,
) -> RuntimeBuildArtifacts:
    injection = InjectionRegistry()
    injection.register_factory(
        "service",
        BootstrapSupervisor,
        lambda _supervisor=supervisor: _supervisor,
    )
    injection.register_factory(
        "service",
        ReplyCoordinatorService,
        lambda _waiter=waiter: legacy_reply_coordinator(reply_waiter=_waiter),
    )
    scope = injection.instantiate_for_scenario("scenario")
    return RuntimeBuildArtifacts(
        scenario=type("S", (), {"steps": []})(),
        inputs=list(inputs),
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
        runtime=runtime,
    )


def test_p5pre_smoke_01_four_group_topology_executes_end_to_end(tmp_path: Path) -> None:
    # P5PRE-SMOKE-01: deterministic 4-group process-supervisor topology should complete terminal reply.
    pkg_name = "phase5pre_stepg_smoke_pkg"
    pkg = tmp_path / pkg_name
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.routing.envelope import Envelope",
                "from stream_kernel.platform.services.reply_waiter import TerminalEvent",
                "",
                "@node(name='ingress.n1', consumes=[], emits=[])",
                "def ingress_n1(payload, ctx):",
                "    _ = ctx",
                "    return [Envelope(payload={'v': payload['v'] + 1}, target='features.n2')]",
                "",
                "@node(name='features.n2', consumes=[], emits=[])",
                "def features_n2(payload, ctx):",
                "    _ = ctx",
                "    return [Envelope(payload={'v': payload['v'] + 1}, target='policy.n3')]",
                "",
                "@node(name='policy.n3', consumes=[], emits=[])",
                "def policy_n3(payload, ctx):",
                "    _ = ctx",
                "    return [Envelope(payload={'v': payload['v'] + 1}, target='egress.n4')]",
                "",
                "@node(name='egress.n4', consumes=[], emits=[])",
                "def egress_n4(payload, ctx):",
                "    _ = ctx",
                "    return [TerminalEvent(status='success', payload={'v': payload['v'] + 1})]",
                "",
            ]
        ),
    )

    sys.path.insert(0, str(tmp_path))
    try:
        runtime = _runtime_for_smoke(module_name=pkg_name)
        supervisor = MultiprocessBootstrapSupervisor()
        waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
        waiter.register(trace_id="t1", reply_to="http:req-1", timeout_seconds=30)
        artifacts = _artifacts_for_smoke(
            runtime=runtime,
            supervisor=supervisor,
            inputs=[
                Envelope(
                    payload={"v": 1},
                    target="ingress.n1",
                    trace_id="t1",
                    reply_to="http:req-1",
                )
            ],
            waiter=waiter,
        )
        execute_runtime_artifacts(artifacts)

        assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"v": 5})

        events = supervisor.lifecycle_events()
        spawned = [event["group_name"] for event in events if event.get("kind") == "worker_spawned"]
        assert spawned == [
            "execution.ingress",
            "execution.features",
            "execution.policy",
            "execution.egress",
        ]
        ready = [event for event in events if event.get("kind") == "worker_ready"]
        stopped = [event for event in events if event.get("kind") == "worker_stopped"]
        assert len(ready) == 4
        assert len(stopped) == 4
    finally:
        sys.path.remove(str(tmp_path))
