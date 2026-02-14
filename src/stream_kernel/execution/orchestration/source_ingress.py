from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.application_context.injection_registry import InjectionRegistryError
from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.platform.services.api.policy import RateLimiterService
from stream_kernel.platform.services.state.context import ContextService
from stream_kernel.platform.services.observability import ObservabilityService
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.routing.envelope import Envelope

_NO_PAYLOAD = object()
WEB_INGRESS_LIMITER_QUALIFIER = "web.ingress.default"


@dataclass(frozen=True, slots=True)
class BootstrapControl:
    # Framework control payload used to trigger source nodes on routing rails.
    target: str


@dataclass(slots=True)
class SourceBootstrapNode:
    # Source adapter wrapper executed inside runner graph.
    role: str
    node_name: str
    adapter: object
    context_service: ContextService
    run_id: str
    scenario_id: str
    ingress_limiter: object | None = None
    observability: object | None = None
    ingress_limiter_qualifier: str | None = None
    _sequence: int = 0
    _iterator: object | None = None
    _next_payload: object = _NO_PAYLOAD
    _primed: bool = False
    exhausted: bool = False

    def __call__(self, _msg: object, _ctx: object | None) -> list[Envelope]:
        if not self._primed:
            self._prime_next()
            self._primed = True

        if self.exhausted or self._next_payload is _NO_PAYLOAD:
            return []

        raw_item = self._next_payload
        self._prime_next()
        self._sequence += 1
        payload, trace_hint, reply_to, span_id = self._normalize_ingress_item(raw_item)
        trace_id = trace_hint or f"{self.run_id}:{self.role}:{self._sequence}"

        limiter = self._ingress_limiter()
        if limiter is not None:
            limiter_key = reply_to or trace_id
            allowed = bool(limiter.allow(key=limiter_key))
            self._emit_limiter_decision(
                trace_id=trace_id,
                allowed=allowed,
            )
            if not allowed:
                outputs = [
                    Envelope(
                        payload=TerminalEvent(
                            status="error",
                            payload={"code": "rate_limited", "status_code": 429},
                            error="rate_limited",
                        ),
                        trace_id=trace_id,
                        reply_to=reply_to,
                        span_id=span_id,
                    )
                ]
                if not self.exhausted:
                    outputs.append(
                        Envelope(
                            payload=BootstrapControl(target=self.node_name),
                            target=self.node_name,
                        )
                    )
                return outputs

        self._seed_context(
            trace_id=trace_id,
            payload=payload,
            reply_to=reply_to,
        )
        self._emit_ingress(
            trace_id=trace_id,
            reply_to=reply_to,
        )

        outputs = [
            Envelope(
                payload=payload,
                trace_id=trace_id,
                reply_to=reply_to,
                span_id=span_id,
            )
        ]
        if not self.exhausted:
            # Re-schedule source polling on the same rails as regular node routing.
            outputs.append(
                Envelope(
                    payload=BootstrapControl(target=self.node_name),
                    target=self.node_name,
                )
            )
        return outputs

    @staticmethod
    def _normalize_ingress_item(raw_item: object) -> tuple[object, str | None, str | None, str | None]:
        if isinstance(raw_item, Envelope):
            trace_id = raw_item.trace_id if isinstance(raw_item.trace_id, str) and raw_item.trace_id else None
            reply_to = raw_item.reply_to if isinstance(raw_item.reply_to, str) and raw_item.reply_to else None
            span_id = raw_item.span_id if isinstance(raw_item.span_id, str) and raw_item.span_id else None
            return raw_item.payload, trace_id, reply_to, span_id
        return raw_item, None, None, None

    def _seed_context(
        self,
        *,
        trace_id: str,
        payload: object,
        reply_to: str | None,
    ) -> None:
        if reply_to is None:
            self.context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=self.run_id,
                scenario_id=self.scenario_id,
            )
            return
        try:
            self.context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=self.run_id,
                scenario_id=self.scenario_id,
                reply_to=reply_to,
            )
        except TypeError:
            self.context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=self.run_id,
                scenario_id=self.scenario_id,
            )

    def _ingress_limiter(self) -> RateLimiterService | None:
        candidate = self.ingress_limiter
        if isinstance(candidate, RateLimiterService):
            return candidate
        if callable(getattr(candidate, "allow", None)):
            return candidate  # type: ignore[return-value]
        return None

    def _emit_ingress(self, *, trace_id: str | None, reply_to: str | None) -> None:
        on_ingress = getattr(self.observability, "on_ingress", None)
        if callable(on_ingress):
            on_ingress(trace_id=trace_id, reply_to=reply_to)

    def _emit_limiter_decision(self, *, trace_id: str | None, allowed: bool) -> None:
        emit = getattr(self.observability, "on_ingress_rate_limit_decision", None)
        if callable(emit):
            emit(
                trace_id=trace_id,
                allowed=allowed,
                source_node=self.node_name,
                source_role=self.role,
                limiter_profile=self.ingress_limiter_qualifier,
            )

    def _prime_next(self) -> None:
        if self.exhausted:
            return
        if self._iterator is None:
            read = getattr(self.adapter, "read", None)
            if not callable(read):
                self.exhausted = True
                self._next_payload = _NO_PAYLOAD
                return
            self._iterator = iter(read())
        try:
            self._next_payload = next(self._iterator)
        except StopIteration:
            self.exhausted = True
            self._next_payload = _NO_PAYLOAD


@dataclass(frozen=True, slots=True)
class SourceIngressPlan:
    # Source ingress runtime artifacts attached to scenario execution.
    source_steps: list[StepSpec] = field(default_factory=list)
    source_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    bootstrap_inputs: list[Envelope] = field(default_factory=list)
    source_node_names: set[str] = field(default_factory=set)


def build_source_ingress_plan(
    *,
    adapters: dict[str, object],
    adapter_instances: dict[str, object],
    adapter_registry: AdapterRegistry | None,
    scenario_scope: ScenarioScope,
    run_id: str,
    scenario_id: str,
    runtime: dict[str, object] | None = None,
) -> SourceIngressPlan:
    # Build executable source ingress nodes from adapter contracts (consumes=[] and emits!=[]).
    context_service = scenario_scope.resolve("service", ContextService)
    observability = _resolve_observability_service(scenario_scope)
    ingress_limiter, limiter_qualifier = _resolve_web_ingress_limiter(
        scenario_scope=scenario_scope,
        runtime=runtime,
    )
    source_nodes: dict[str, object] = {}
    source_consumers: dict[type[Any], list[str]] = {}
    for role in sorted(adapter_instances.keys()):
        cfg = adapters.get(role)
        if not isinstance(cfg, dict):
            continue
        meta = _resolve_adapter_meta(role, adapter_registry=adapter_registry)
        adapter = adapter_instances[role]
        has_reader = callable(getattr(adapter, "read", None))
        if not has_reader:
            continue
        if meta is not None and (meta.consumes or not meta.emits):
            continue
        node_name = f"source:{role}"
        source_nodes[node_name] = SourceBootstrapNode(
            role=role,
            node_name=node_name,
            adapter=adapter,
            context_service=context_service,
            run_id=run_id,
            scenario_id=scenario_id,
            ingress_limiter=ingress_limiter,
            observability=observability,
            ingress_limiter_qualifier=limiter_qualifier,
        )
        source_consumers.setdefault(BootstrapControl, []).append(node_name)
    if adapters and not source_nodes:
        raise ValueError("at least one source adapter (consumes=[], emits!=[]) with read() must be configured")

    source_steps = [StepSpec(name=name, step=step) for name, step in source_nodes.items()]
    bootstrap_inputs = [
        Envelope(payload=BootstrapControl(target=target), target=target)
        for target in source_nodes.keys()
    ]
    return SourceIngressPlan(
        source_steps=source_steps,
        source_consumers=source_consumers,
        bootstrap_inputs=bootstrap_inputs,
        source_node_names=set(source_nodes.keys()),
    )


def _resolve_adapter_meta(role: str, *, adapter_registry: AdapterRegistry | None):
    if adapter_registry is not None:
        meta = adapter_registry.get_meta(role, role)
        if meta is not None:
            return meta
    return None


def _resolve_web_ingress_limiter(
    *,
    scenario_scope: ScenarioScope,
    runtime: dict[str, object] | None,
) -> tuple[RateLimiterService | None, str | None]:
    if not _runtime_has_web_ingress_rate_limit(runtime):
        return None, None
    try:
        resolved = scenario_scope.resolve(
            "service",
            RateLimiterService,
            qualifier=WEB_INGRESS_LIMITER_QUALIFIER,
        )
    except InjectionRegistryError:
        return None, None
    if isinstance(resolved, RateLimiterService):
        return resolved, WEB_INGRESS_LIMITER_QUALIFIER
    if callable(getattr(resolved, "allow", None)):
        return resolved, WEB_INGRESS_LIMITER_QUALIFIER  # type: ignore[return-value]
    return None, None


def _resolve_observability_service(scenario_scope: ScenarioScope) -> object | None:
    try:
        return scenario_scope.resolve("service", ObservabilityService)
    except InjectionRegistryError:
        return None


def _runtime_has_web_ingress_rate_limit(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    web = runtime.get("web")
    if not isinstance(web, dict):
        return False
    interfaces = web.get("interfaces")
    if not isinstance(interfaces, list):
        return False
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        policies = interface.get("policies")
        if not isinstance(policies, dict):
            continue
        rate_limit = policies.get("rate_limit")
        if isinstance(rate_limit, dict):
            return True
    return False
