from __future__ import annotations

import sys
from pathlib import Path

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.builder import RuntimeBuildArtifacts, execute_runtime_artifacts
from stream_kernel.platform.services.runtime.bootstrap import BootstrapSupervisor, MultiprocessBootstrapSupervisor
from stream_kernel.platform.services.messaging.reply_coordinator import ReplyCoordinatorService, legacy_reply_coordinator
from stream_kernel.platform.services.messaging.reply_waiter import InMemoryReplyWaiterService, TerminalEvent
from stream_kernel.routing.envelope import Envelope


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _runtime_for_smoke(*, module_name: str) -> dict[str, object]:
    return {
        "discovery_modules": [module_name],
        "observability": {},
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


def _extract_prom_metric_value(payload: str, metric_name: str) -> float | None:
    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(metric_name + "{"):
            try:
                return float(stripped.rsplit(" ", 1)[-1])
            except ValueError:
                return None
        if stripped.startswith(metric_name + " "):
            try:
                return float(stripped.split(" ", 1)[1])
            except ValueError:
                return None
    return None


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
                "from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent",
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


def test_p5pre_smoke_02_multiprocess_emits_monitoring_metrics_with_drained_close_end(tmp_path: Path) -> None:
    # P5PRE-SMOKE-02: multiprocess supervisor should publish non-zero dispatch/sink metrics and final pending=0.
    pkg_name = "phase5pre_stepg_metrics_pkg"
    pkg = tmp_path / pkg_name
    traces_path = tmp_path / "trace_metrics_smoke.jsonl"
    metrics_path = tmp_path / "metrics.prom"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "\n".join(
            [
                "from stream_kernel.kernel.node_annotation import node",
                "from stream_kernel.routing.envelope import Envelope",
                "from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent",
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
        runtime["observability"] = {
            "tracing": {
                "dispatch_queue": {"max_items": 1024, "drop_policy": "block_with_timeout", "block_timeout_ms": 20},
                "exporters": [
                    {
                        "kind": "jsonl",
                        "settings": {"path": str(traces_path), "write_mode": "line", "flush_every_n": 1},
                    }
                ],
            },
            "monitoring": {
                "exporters": [
                    {
                        "kind": "prometheus",
                        "settings": {
                            "mode": "textfile",
                            "textfile": {"path": str(metrics_path)},
                        },
                    }
                ]
            },
        }

        supervisor = MultiprocessBootstrapSupervisor()
        waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
        waiter.register(trace_id="t2", reply_to="http:req-2", timeout_seconds=30)
        artifacts = _artifacts_for_smoke(
            runtime=runtime,
            supervisor=supervisor,
            inputs=[
                Envelope(
                    payload={"v": 1},
                    target="ingress.n1",
                    trace_id="t2",
                    reply_to="http:req-2",
                )
            ],
            waiter=waiter,
        )
        execute_runtime_artifacts(artifacts)

        assert waiter.poll(trace_id="t2") == TerminalEvent(status="success", payload={"v": 5})
        assert traces_path.exists()
        assert metrics_path.exists()

        payload = metrics_path.read_text(encoding="utf-8")
        dispatch_submitted = _extract_prom_metric_value(
            payload,
            "stream_kernel_observability_dispatch_submitted_total",
        )
        sink_exported = _extract_prom_metric_value(
            payload,
            "stream_kernel_observability_sink_exported_total",
        )
        pending_total = _extract_prom_metric_value(
            payload,
            "stream_kernel_observability_pending_total_estimate",
        )
        loss_total = _extract_prom_metric_value(
            payload,
            "stream_kernel_observability_loss_estimate_total",
        )

        assert isinstance(dispatch_submitted, float)
        assert dispatch_submitted > 0
        assert isinstance(sink_exported, float)
        assert sink_exported > 0
        assert pending_total == 0
        assert loss_total == 0
        assert 'stream_kernel_observability_lifecycle_stage_active{component="trace_dispatch",scope="observability_transport",stage="close_end"} 1' in payload

        events = supervisor.lifecycle_events()
        diagnostics = [event for event in events if event.get("kind") == "trace_dispatch_diagnostics"]
        assert diagnostics
        assert diagnostics[-1].get("stage") == "close_end"
        assert diagnostics[-1].get("pending_total_estimate") == 0
    finally:
        sys.path.remove(str(tmp_path))
