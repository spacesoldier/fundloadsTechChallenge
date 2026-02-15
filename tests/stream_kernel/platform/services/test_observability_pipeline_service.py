from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.observability import (
    FanoutObservabilityService,
    NoOpObservabilityService,
    ReplyAwareObservabilityService,
)


def test_obs_k_c_01_noop_service_supports_pipeline_callbacks() -> None:
    service = NoOpObservabilityService()

    assert (
        service.before_node(node_name="n1", payload={"v": 1}, ctx={}, trace_id="t1")
        is None
    )
    service.after_node(
        node_name="n1",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=None,
    )
    service.on_node_error(
        node_name="n1",
        payload={"v": 1},
        ctx={},
        trace_id="t1",
        error=RuntimeError("boom"),
        state=None,
    )
    service.on_run_end()
    service.publish_trace(event={"span": "node"}, trace_id="t1", attributes={"k": "v"})
    service.publish_log(event={"msg": "hello"}, trace_id="t1", attributes={"level": "info"})
    service.publish_metric(event={"name": "latency_ms", "value": 12}, trace_id="t1")
    service.publish_monitoring(event={"kind": "health", "status": "ok"}, trace_id="t1")
    service.on_ingress(trace_id="t1", reply_to="http:req-1")
    service.on_terminal_event(
        trace_id="t1",
        terminal_event=TerminalEvent(status="success", payload={"ok": True}),
    )
    service.on_ingress_rate_limit_decision(
        trace_id="t1",
        allowed=True,
        source_node="source:events",
        source_role="events",
        limiter_profile="web.ingress.default",
    )
    service.on_outbound_policy_decision(
        stage="retry",
        decision="scheduled",
        trace_id="t1",
        key="partner-a",
        profile="partner_api",
        attempt=1,
    )
    service.on_runtime_lifecycle_event(
        event="runtime_ready",
        process_group="execution.cpu",
        details={"ready": True},
    )


@dataclass
class _RecorderObserver:
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def before_node(self, *, node_name: str, payload: object, ctx: dict[str, object], trace_id: str | None) -> object:
        _ = (node_name, payload, ctx, trace_id)
        return None

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> None:
        _ = (node_name, payload, ctx, trace_id, outputs, state)
        return None

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        _ = (node_name, payload, ctx, trace_id, error, state)
        return None

    def on_run_end(self) -> None:
        return None

    def on_ingress(self, *, trace_id: str | None, reply_to: str | None) -> None:
        self.events.append(("ingress", {"trace_id": trace_id, "reply_to": reply_to}))

    def on_trace_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "trace_event",
                {
                    "event": event,
                    "trace_id": trace_id,
                    "attributes": dict(attributes or {}),
                },
            )
        )

    def on_log_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "log_event",
                {
                    "event": event,
                    "trace_id": trace_id,
                    "attributes": dict(attributes or {}),
                },
            )
        )

    def on_metric_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "metric_event",
                {
                    "event": event,
                    "trace_id": trace_id,
                    "attributes": dict(attributes or {}),
                },
            )
        )

    def on_monitoring_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "monitor_event",
                {
                    "event": event,
                    "trace_id": trace_id,
                    "attributes": dict(attributes or {}),
                },
            )
        )

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        self.events.append(
            (
                "ingress_limit",
                {
                    "trace_id": trace_id,
                    "allowed": allowed,
                    "source_node": source_node,
                    "source_role": source_role,
                    "limiter_profile": limiter_profile,
                },
            )
        )

    def on_outbound_policy_decision(
        self,
        *,
        stage: str,
        decision: str,
        trace_id: str | None,
        key: str | None,
        profile: str | None,
        attempt: int,
    ) -> None:
        self.events.append(
            (
                "outbound_policy",
                {
                    "stage": stage,
                    "decision": decision,
                    "trace_id": trace_id,
                    "key": key,
                    "profile": profile,
                    "attempt": attempt,
                },
            )
        )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "runtime_lifecycle",
                {
                    "event": event,
                    "process_group": process_group,
                    "details": dict(details or {}),
                },
            )
        )


def test_obs_k_c_02_fanout_service_forwards_pipeline_callbacks() -> None:
    observer = _RecorderObserver()
    service = FanoutObservabilityService(observers=[observer])

    service.publish_trace(event={"span": "node"}, trace_id="t1", attributes={"k": "v"})
    service.publish_log(event={"msg": "hello"}, trace_id="t1")
    service.publish_metric(event={"name": "latency_ms"}, trace_id="t1")
    service.publish_monitoring(event={"kind": "health"}, trace_id="t1")
    service.on_ingress(trace_id="t1", reply_to="http:req-1")
    service.on_ingress_rate_limit_decision(
        trace_id="t1",
        allowed=False,
        source_node="source:events",
        source_role="events",
        limiter_profile="web.ingress.default",
    )
    service.on_outbound_policy_decision(
        stage="limiter",
        decision="deny",
        trace_id="t1",
        key="partner-a",
        profile="partner_api",
        attempt=2,
    )
    service.on_runtime_lifecycle_event(
        event="runtime_stopped",
        process_group="execution.cpu",
        details={"mode": "graceful"},
    )

    kinds = [item[0] for item in observer.events]
    assert kinds == [
        "trace_event",
        "log_event",
        "metric_event",
        "monitor_event",
        "ingress",
        "ingress_limit",
        "outbound_policy",
        "runtime_lifecycle",
    ]


def test_obs_k_f_01_fanout_isolates_optional_callback_failures() -> None:
    # OBS-K-F-01: one failing optional callback must not break fan-out delivery to other observers.
    @dataclass
    class _FailingObserver:
        trace_calls: int = 0

        def before_node(self, **kwargs: object) -> object | None:
            _ = kwargs
            return None

        def after_node(self, **kwargs: object) -> None:
            _ = kwargs
            return None

        def on_node_error(self, **kwargs: object) -> None:
            _ = kwargs
            return None

        def on_run_end(self) -> None:
            return None

        def on_trace_event(
            self,
            *,
            event: object,
            trace_id: str | None,
            attributes: dict[str, object] | None,
        ) -> None:
            _ = (event, trace_id, attributes)
            self.trace_calls += 1
            raise RuntimeError("sink failed")

    failing = _FailingObserver()
    recorder = _RecorderObserver()
    service = FanoutObservabilityService(observers=[failing, recorder])

    # Must not raise despite failing observer.
    service.publish_trace(event={"span": "node"}, trace_id="t1", attributes={"k": "v"})

    assert failing.trace_calls == 1
    assert recorder.events[0][0] == "trace_event"


def test_obs_k_c_03_reply_aware_forwards_pipeline_callbacks() -> None:
    @dataclass
    class _Inner(NoOpObservabilityService):
        ingress_calls: int = 0
        terminal_calls: int = 0
        limiter_calls: int = 0
        outbound_calls: int = 0
        lifecycle_calls: int = 0

        def on_ingress(self, *, trace_id: str | None, reply_to: str | None) -> None:
            _ = (trace_id, reply_to)
            self.ingress_calls += 1

        def publish_trace(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            _ = (event, trace_id, attributes)
            self.lifecycle_calls += 1

        def on_terminal_event(
            self,
            *,
            trace_id: str | None,
            terminal_event: TerminalEvent | None,
        ) -> None:
            _ = (trace_id, terminal_event)
            self.terminal_calls += 1

        def on_ingress_rate_limit_decision(
            self,
            *,
            trace_id: str | None,
            allowed: bool,
            source_node: str | None,
            source_role: str | None,
            limiter_profile: str | None,
        ) -> None:
            _ = (trace_id, allowed, source_node, source_role, limiter_profile)
            self.limiter_calls += 1

        def on_outbound_policy_decision(
            self,
            *,
            stage: str,
            decision: str,
            trace_id: str | None,
            key: str | None,
            profile: str | None,
            attempt: int,
        ) -> None:
            _ = (stage, decision, trace_id, key, profile, attempt)
            self.outbound_calls += 1

        def on_runtime_lifecycle_event(
            self,
            *,
            event: str,
            process_group: str | None,
            details: dict[str, object] | None,
        ) -> None:
            _ = (event, process_group, details)
            self.lifecycle_calls += 1

    @dataclass
    class _ReplyCoordinator:
        register_calls: int = 0
        complete_calls: int = 0

        def register_if_requested(self, *, trace_id: str | None, reply_to: str | None) -> None:
            _ = (trace_id, reply_to)
            self.register_calls += 1

        def complete_if_waiting(
            self,
            *,
            trace_id: str | None,
            terminal_event: TerminalEvent | None,
        ) -> None:
            _ = (trace_id, terminal_event)
            self.complete_calls += 1

    inner = _Inner()
    reply = _ReplyCoordinator()
    service = ReplyAwareObservabilityService(inner=inner, reply_coordinator=reply)

    service.publish_trace(event={"span": "node"}, trace_id="t1")
    service.on_ingress(trace_id="t1", reply_to="http:req-1")
    service.on_terminal_event(
        trace_id="t1",
        terminal_event=TerminalEvent(status="success", payload={"ok": True}),
    )
    service.on_ingress_rate_limit_decision(
        trace_id="t1",
        allowed=True,
        source_node="source:events",
        source_role="events",
        limiter_profile="web.ingress.default",
    )
    service.on_outbound_policy_decision(
        stage="retry",
        decision="scheduled",
        trace_id="t1",
        key="partner-a",
        profile="partner_api",
        attempt=1,
    )
    service.on_runtime_lifecycle_event(
        event="runtime_ready",
        process_group="execution.cpu",
        details={"ready": True},
    )

    assert inner.ingress_calls == 1
    assert inner.terminal_calls == 1
    assert inner.limiter_calls == 1
    assert inner.outbound_calls == 1
    assert inner.lifecycle_calls == 2
    assert reply.register_calls == 1
    assert reply.complete_calls == 1
