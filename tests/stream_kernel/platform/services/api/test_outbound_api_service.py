from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.api.outbound import (
    InMemoryOutboundApiService,
    OutboundCircuitOpenError,
    OutboundRateLimitedError,
)
from stream_kernel.platform.services.api.policy import InMemoryRateLimiterService


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def _run_sequence(
    service: InMemoryOutboundApiService,
    *,
    trace_prefix: str,
) -> dict[str, object]:
    outcomes: list[str] = []
    for idx in range(3):
        key = "partner-a"
        try:
            value = service.call(
                operation=lambda i=idx: (_ for _ in ()).throw(RuntimeError("boom")) if i == 1 else f"ok-{i}",
                key=key,
                trace_id=f"{trace_prefix}-{idx}",
            )
            outcomes.append(f"ok:{value}")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"err:{type(exc).__name__}:{exc}")
    return {
        "outcomes": outcomes,
        "counters": service.diagnostics_counters(),
        "events": [
            {k: v for k, v in item.items() if k != "ts_epoch_seconds"}
            for item in service.diagnostic_events()
        ],
    }


def test_api_egr_01_outbound_call_is_blocked_by_limiter() -> None:
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
    )
    service = InMemoryOutboundApiService(
        policy_config={"rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}},
        limiter=limiter,
    )
    calls: list[int] = []

    def _ok() -> str:
        calls.append(1)
        return "ok"

    assert service.call(operation=_ok, key="partner-a") == "ok"
    with pytest.raises(OutboundRateLimitedError):
        service.call(operation=_ok, key="partner-a")
    assert len(calls) == 1


def test_api_egr_02_retry_budget_is_bounded_and_deterministic() -> None:
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 2, "window_ms": 60_000},
    )
    service = InMemoryOutboundApiService(
        policy_config={
            "retry": {"max_attempts": 2, "backoff_ms": 0},
            "rate_limit": {"kind": "fixed_window", "limit": 2, "window_ms": 60_000},
        },
        limiter=limiter,
    )
    attempts: list[int] = []

    def _always_fail() -> str:
        attempts.append(1)
        raise RuntimeError("boom")

    with pytest.raises(OutboundRateLimitedError):
        service.call(operation=_always_fail, key="partner-a")
    # retry contract is deterministic: only two transport attempts happen before limiter blocks the third.
    assert len(attempts) == 2


def test_api_egr_03_limiter_then_circuit_gate_order_is_deterministic() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
        now_fn=clock,
    )
    service = InMemoryOutboundApiService(
        policy_config={
            "retry": {"max_attempts": 0, "backoff_ms": 0},
            "circuit_breaker": {
                "failure_threshold": 1,
                "reset_timeout_ms": 60_000,
                "half_open_max_calls": 1,
            },
            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
        },
        limiter=limiter,
        store=InMemoryKvStore(),
        now_fn=clock,
    )

    with pytest.raises(RuntimeError):
        service.call(operation=lambda: (_ for _ in ()).throw(RuntimeError("fail")), key="partner-a")

    # second call: limiter is already exceeded; order must be limiter -> circuit gate, so we get rate-limit error.
    with pytest.raises(OutboundRateLimitedError):
        service.call(operation=lambda: "ok", key="partner-a")

    # circuit-open error exists, but should not win over limiter on this path.
    fresh = InMemoryOutboundApiService(
        policy_config={
            "retry": {"max_attempts": 0, "backoff_ms": 0},
            "circuit_breaker": {
                "failure_threshold": 1,
                "reset_timeout_ms": 60_000,
                "half_open_max_calls": 1,
            },
        },
        limiter=None,
        store=InMemoryKvStore(),
        now_fn=clock,
    )
    with pytest.raises(RuntimeError):
        fresh.call(operation=lambda: (_ for _ in ()).throw(RuntimeError("fail")), key="partner-b")
    with pytest.raises(OutboundCircuitOpenError):
        fresh.call(operation=lambda: "ok", key="partner-b")


def test_api_obs_01_outbound_service_exports_limiter_counters() -> None:
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
    )
    service = InMemoryOutboundApiService(
        policy_config={"rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}},
        limiter=limiter,
    )
    assert service.call(operation=lambda: "ok", key="partner-a") == "ok"
    with pytest.raises(OutboundRateLimitedError):
        service.call(operation=lambda: "ok", key="partner-a")

    counters = service.diagnostics_counters()
    assert counters["allowed"] == 1
    assert counters["blocked"] == 1
    assert counters["queued"] == 0
    assert counters["dropped"] == 1


def test_api_obs_02_outbound_service_emits_policy_decision_markers() -> None:
    decisions: list[dict[str, object]] = []

    class _Obs:
        def on_outbound_policy_decision(self, **kwargs: object) -> None:
            decisions.append(dict(kwargs))

    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 3, "window_ms": 60_000},
    )
    service = InMemoryOutboundApiService(
        policy_config={
            "retry": {"max_attempts": 1, "backoff_ms": 0},
            "rate_limit": {"kind": "fixed_window", "limit": 3, "window_ms": 60_000},
        },
        limiter=limiter,
        observability=_Obs(),
    )
    with pytest.raises(RuntimeError):
        service.call(
            operation=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            key="partner-a",
            trace_id="trace-1",
        )

    stages = [item.get("stage") for item in decisions]
    assert "limiter" in stages
    assert "retry" in stages
    assert all(item.get("trace_id") == "trace-1" for item in decisions)


def test_api_obs_03_outbound_service_diagnostics_redact_sensitive_auth_headers() -> None:
    service = InMemoryOutboundApiService(
        policy_config={
            "auth": {
                "mode": "token",
                "token": "super-secret-token",
                "headers": {"Authorization": "Bearer abc123", "X-Api-Key": "my-key"},
            },
            "telemetry": {"headers": {"x-trace-secret": "secret-value"}},
            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
        },
        limiter=InMemoryRateLimiterService(
            limiter_config={"kind": "fixed_window", "limit": 1, "window_ms": 60_000},
        ),
    )
    assert service.call(operation=lambda: "ok", key="partner-a") == "ok"
    with pytest.raises(OutboundRateLimitedError):
        service.call(operation=lambda: "ok", key="partner-a")

    events = service.diagnostic_events()
    assert events
    snapshot = events[-1].get("policy_snapshot")
    assert isinstance(snapshot, dict)
    rendered = str(snapshot)
    assert "super-secret-token" not in rendered
    assert "Bearer abc123" not in rendered
    assert "my-key" not in rendered
    assert "secret-value" not in rendered
    assert "<redacted>" in rendered


def test_api_reg_01_policy_disabled_mode_is_passthrough_and_marker_free() -> None:
    service = InMemoryOutboundApiService(
        policy_config={},
        limiter=None,
    )
    payload = {"x": 1}
    assert service.call(operation=lambda: payload, key="partner-a", trace_id="t-1") is payload
    with pytest.raises(RuntimeError):
        service.call(
            operation=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            key="partner-a",
            trace_id="t-2",
        )

    assert service.diagnostics_counters() == {
        "allowed": 0,
        "blocked": 0,
        "queued": 0,
        "dropped": 0,
    }
    assert service.diagnostic_events() == []


def test_api_reg_02_policy_enabled_mode_is_deterministic_for_same_time_source() -> None:
    clock = _Clock(now=10.0)
    policy = {
        "retry": {"max_attempts": 1, "backoff_ms": 0},
        "rate_limit": {"kind": "fixed_window", "limit": 3, "window_ms": 60_000},
    }

    svc_a = InMemoryOutboundApiService(
        profile="partner_api",
        policy_config=policy,
        limiter=InMemoryRateLimiterService(limiter_config=policy["rate_limit"], now_fn=clock),
        now_fn=clock,
    )
    svc_b = InMemoryOutboundApiService(
        profile="partner_api",
        policy_config=policy,
        limiter=InMemoryRateLimiterService(limiter_config=policy["rate_limit"], now_fn=clock),
        now_fn=clock,
    )

    result_a = _run_sequence(svc_a, trace_prefix="trace-a")
    result_b = _run_sequence(svc_b, trace_prefix="trace-a")
    assert result_a == result_b
