from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.platform.services.api.policy import RateLimiterService

T = TypeVar("T")


class OutboundRateLimitedError(RuntimeError):
    pass


class OutboundCircuitOpenError(RuntimeError):
    pass


@runtime_checkable
class OutboundApiService(Protocol):
    # Platform-owned outbound API policy-chain contract.
    def profile_name(self) -> str | None:
        raise NotImplementedError("OutboundApiService.profile_name must be implemented")

    def policy(self) -> dict[str, object]:
        raise NotImplementedError("OutboundApiService.policy must be implemented")

    def call(
        self,
        *,
        operation: Callable[[], T],
        key: str | None = None,
        trace_id: str | None = None,
    ) -> T:
        raise NotImplementedError("OutboundApiService.call must be implemented")

    def diagnostics_counters(self) -> dict[str, int]:
        raise NotImplementedError("OutboundApiService.diagnostics_counters must be implemented")

    def diagnostic_events(self) -> list[dict[str, object]]:
        raise NotImplementedError("OutboundApiService.diagnostic_events must be implemented")


@service(name="outbound_api_service")
@dataclass(slots=True)
class InMemoryOutboundApiService(OutboundApiService):
    # Baseline deterministic policy-chain for outbound calls.
    profile: str | None = None
    policy_config: dict[str, object] = field(default_factory=dict)
    limiter: object | None = None
    observability: object | None = None
    store: object = field(default_factory=InMemoryKvStore)
    state_prefix: str = "outbound_api"
    now_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep
    max_diagnostic_events: int = 256
    _fallback_store: KVStore = field(default_factory=InMemoryKvStore, init=False, repr=False)
    _counters: dict[str, int] = field(
        default_factory=lambda: {"allowed": 0, "blocked": 0, "queued": 0, "dropped": 0},
        init=False,
        repr=False,
    )
    _events: list[dict[str, object]] = field(default_factory=list, init=False, repr=False)

    def profile_name(self) -> str | None:
        return self.profile

    def policy(self) -> dict[str, object]:
        return dict(self.policy_config)

    def call(
        self,
        *,
        operation: Callable[[], T],
        key: str | None = None,
        trace_id: str | None = None,
    ) -> T:
        retry = self._retry_policy()
        max_attempts = int(retry.get("max_attempts", 0))
        backoff_ms = int(retry.get("backoff_ms", 0))
        total_attempts = 1 + max(0, max_attempts)
        last_error: Exception | None = None
        for attempt_index in range(total_attempts):
            limiter_acquired = self._acquire_limiter(key=key, trace_id=trace_id, attempt=attempt_index + 1)
            try:
                self._guard_circuit(key=key, trace_id=trace_id, attempt=attempt_index + 1)
                self._emit_policy_decision(
                    stage="call",
                    decision="invoke",
                    trace_id=trace_id,
                    key=key,
                    attempt=attempt_index + 1,
                )
                result = operation()
                self._record_success(key=key)
                return result
            except OutboundCircuitOpenError:
                self._increment("dropped")
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._record_failure(key=key)
                if attempt_index >= (total_attempts - 1):
                    raise
                self._increment("queued")
                self._emit_policy_decision(
                    stage="retry",
                    decision="scheduled",
                    trace_id=trace_id,
                    key=key,
                    attempt=attempt_index + 1,
                )
                if backoff_ms > 0:
                    self.sleep_fn(backoff_ms / 1000.0)
            finally:
                if limiter_acquired:
                    self._release_limiter(key=key)
        assert last_error is not None
        raise last_error

    def diagnostics_counters(self) -> dict[str, int]:
        return dict(self._counters)

    def diagnostic_events(self) -> list[dict[str, object]]:
        return [dict(item) for item in self._events]

    def _retry_policy(self) -> dict[str, object]:
        retry = self.policy_config.get("retry", {})
        if isinstance(retry, dict):
            return dict(retry)
        return {}

    def _circuit_policy(self) -> dict[str, object] | None:
        breaker = self.policy_config.get("circuit_breaker", {})
        if isinstance(breaker, dict) and breaker:
            return dict(breaker)
        return None

    def _acquire_limiter(
        self,
        *,
        key: str | None,
        trace_id: str | None,
        attempt: int,
    ) -> bool:
        limiter = self._limiter()
        if limiter is None:
            return False
        allowed = bool(limiter.allow(key=key))
        if not allowed:
            self._increment("blocked")
            self._increment("dropped")
            self._emit_policy_decision(
                stage="limiter",
                decision="deny",
                trace_id=trace_id,
                key=key,
                attempt=attempt,
            )
            raise OutboundRateLimitedError("outbound call blocked by rate limiter")
        self._increment("allowed")
        self._emit_policy_decision(
            stage="limiter",
            decision="allow",
            trace_id=trace_id,
            key=key,
            attempt=attempt,
        )
        return True

    def _release_limiter(self, *, key: str | None) -> None:
        limiter = self._limiter()
        if limiter is None:
            return
        limiter.release(key=key)

    def _limiter(self) -> RateLimiterService | None:
        candidate = self.limiter
        if isinstance(candidate, RateLimiterService):
            return candidate
        if (
            callable(getattr(candidate, "allow", None))
            and callable(getattr(candidate, "release", None))
        ):
            return candidate  # type: ignore[return-value]
        return None

    def _guard_circuit(
        self,
        *,
        key: str | None,
        trace_id: str | None,
        attempt: int,
    ) -> None:
        policy = self._circuit_policy()
        if policy is None:
            return
        state = self._load_circuit_state(key=key)
        now_ms = int(self.now_fn() * 1000.0)
        reset_timeout_ms = int(policy.get("reset_timeout_ms", 30000))
        half_open_max_calls = int(policy.get("half_open_max_calls", 1))
        status = state.get("status", "closed")
        if status == "open":
            opened_at_ms = int(state.get("opened_at_ms", now_ms))
            if (now_ms - opened_at_ms) < reset_timeout_ms:
                self._emit_policy_decision(
                    stage="circuit_breaker",
                    decision="open_deny",
                    trace_id=trace_id,
                    key=key,
                    attempt=attempt,
                )
                raise OutboundCircuitOpenError("outbound circuit breaker is open")
            state["status"] = "half_open"
            state["half_open_calls"] = 0
        if state.get("status") == "half_open":
            calls = int(state.get("half_open_calls", 0))
            if calls >= half_open_max_calls:
                self._emit_policy_decision(
                    stage="circuit_breaker",
                    decision="half_open_deny",
                    trace_id=trace_id,
                    key=key,
                    attempt=attempt,
                )
                raise OutboundCircuitOpenError("outbound circuit breaker is open")
            state["half_open_calls"] = calls + 1
        self._emit_policy_decision(
            stage="circuit_breaker",
            decision="allow",
            trace_id=trace_id,
            key=key,
            attempt=attempt,
        )
        self._save_circuit_state(key=key, state=state)

    def _record_success(self, *, key: str | None) -> None:
        policy = self._circuit_policy()
        if policy is None:
            return
        self._save_circuit_state(
            key=key,
            state={"status": "closed", "failure_count": 0, "opened_at_ms": 0, "half_open_calls": 0},
        )

    def _record_failure(self, *, key: str | None) -> None:
        policy = self._circuit_policy()
        if policy is None:
            return
        failure_threshold = int(policy.get("failure_threshold", 5))
        now_ms = int(self.now_fn() * 1000.0)
        state = self._load_circuit_state(key=key)
        failure_count = int(state.get("failure_count", 0)) + 1
        if failure_count >= failure_threshold:
            self._save_circuit_state(
                key=key,
                state={
                    "status": "open",
                    "failure_count": failure_count,
                    "opened_at_ms": now_ms,
                    "half_open_calls": int(state.get("half_open_calls", 0)),
                },
            )
            return
        state["status"] = "closed"
        state["failure_count"] = failure_count
        self._save_circuit_state(key=key, state=state)

    def _state_key(self, *, key: str | None) -> str:
        profile = self.profile or "default"
        scope = key or "__global__"
        return f"{self.state_prefix}:{profile}:circuit:{scope}"

    def _load_circuit_state(self, *, key: str | None) -> dict[str, object]:
        store = self._kv_store()
        raw = store.get(self._state_key(key=key))
        if isinstance(raw, dict):
            return dict(raw)
        return {"status": "closed", "failure_count": 0, "opened_at_ms": 0, "half_open_calls": 0}

    def _save_circuit_state(self, *, key: str | None, state: dict[str, object]) -> None:
        self._kv_store().set(self._state_key(key=key), dict(state))

    def _kv_store(self) -> KVStore:
        candidate = self.store
        if isinstance(candidate, KVStore):
            return candidate
        if (
            callable(getattr(candidate, "get", None))
            and callable(getattr(candidate, "set", None))
            and callable(getattr(candidate, "delete", None))
        ):
            return candidate  # type: ignore[return-value]
        return self._fallback_store

    def _emit_policy_decision(
        self,
        *,
        stage: str,
        decision: str,
        trace_id: str | None,
        key: str | None,
        attempt: int,
    ) -> None:
        if not self._policy_observation_enabled():
            return
        event: dict[str, object] = {
            "kind": "policy_decision",
            "stage": stage,
            "decision": decision,
            "trace_id": trace_id,
            "key": key,
            "profile": self.profile,
            "attempt": attempt,
            "ts_epoch_seconds": int(self.now_fn()),
            "policy_snapshot": _redact_sensitive_mapping(self.policy_config),
        }
        self._events.append(event)
        if len(self._events) > max(16, int(self.max_diagnostic_events)):
            self._events = self._events[-max(16, int(self.max_diagnostic_events)) :]
        callback = getattr(self.observability, "on_outbound_policy_decision", None)
        if callable(callback):
            callback(
                stage=stage,
                decision=decision,
                trace_id=trace_id,
                key=key,
                profile=self.profile,
                attempt=attempt,
            )

    def _increment(self, key: str) -> None:
        self._counters[key] = self._counters.get(key, 0) + 1

    def _policy_observation_enabled(self) -> bool:
        return isinstance(self.policy_config, dict) and bool(self.policy_config)


_SENSITIVE_KEY_TOKENS = {
    "secret",
    "token",
    "authorization",
    "apikey",
    "api_key",
    "password",
    "passwd",
    "pwd",
    "cookie",
    "setcookie",
    "headers",
    "signature",
}


def _redact_sensitive_mapping(value: object) -> object:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                out[str(key)] = _redact_sensitive_mapping(item)
                continue
            normalized = key.lower().replace("-", "").replace("_", "")
            if any(token.replace("_", "") in normalized for token in _SENSITIVE_KEY_TOKENS):
                out[key] = "<redacted>"
                continue
            out[key] = _redact_sensitive_mapping(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive_mapping(item) for item in value]
    return value
