from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Condition


class ExecutionIpcFlowControlPolicy:
    requires_ack: bool = False

    def acquire(self, target_id: str, count: int) -> None:
        raise NotImplementedError

    def release(self, target_id: str, count: int) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class NoopFlowControlPolicy(ExecutionIpcFlowControlPolicy):
    requires_ack: bool = False

    def acquire(self, target_id: str, count: int) -> None:
        _ = target_id, count

    def release(self, target_id: str, count: int) -> None:
        _ = target_id, count


@dataclass(slots=True)
class CreditWindowFlowControlPolicy(ExecutionIpcFlowControlPolicy):
    window_size: int
    requires_ack: bool = True

    def __post_init__(self) -> None:
        if self.window_size <= 0:
            raise ValueError("CreditWindowFlowControlPolicy.window_size must be > 0")
        self._condition = Condition()
        self._inflight: dict[str, int] = {}

    def acquire(self, target_id: str, count: int) -> None:
        if count <= 0:
            return
        with self._condition:
            while self._inflight.get(target_id, 0) + count > self.window_size:
                self._condition.wait(timeout=0.05)
            self._inflight[target_id] = self._inflight.get(target_id, 0) + count

    def release(self, target_id: str, count: int) -> None:
        if count <= 0:
            return
        with self._condition:
            current = self._inflight.get(target_id, 0)
            remaining = max(0, current - count)
            if remaining == 0:
                self._inflight.pop(target_id, None)
            else:
                self._inflight[target_id] = remaining
            self._condition.notify_all()


@dataclass(slots=True)
class TokenBucketFlowControlPolicy(ExecutionIpcFlowControlPolicy):
    rate_per_sec: float
    burst: float
    requires_ack: bool = False

    def __post_init__(self) -> None:
        if self.rate_per_sec <= 0:
            raise ValueError("TokenBucketFlowControlPolicy.rate_per_sec must be > 0")
        if self.burst <= 0:
            raise ValueError("TokenBucketFlowControlPolicy.burst must be > 0")
        self._condition = Condition()
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def acquire(self, target_id: str, count: int) -> None:
        if count <= 0:
            return
        with self._condition:
            while True:
                tokens = self._refill(target_id)
                if tokens >= count:
                    self._tokens[target_id] = tokens - count
                    return
                missing = count - tokens
                wait_seconds = max(0.001, missing / self.rate_per_sec)
                self._condition.wait(timeout=wait_seconds)

    def release(self, target_id: str, count: int) -> None:
        _ = target_id, count

    def _refill(self, target_id: str) -> float:
        now = time.monotonic()
        last = self._last_refill.get(target_id, now)
        elapsed = max(0.0, now - last)
        tokens = self._tokens.get(target_id, self.burst)
        tokens = min(self.burst, tokens + elapsed * self.rate_per_sec)
        self._tokens[target_id] = tokens
        self._last_refill[target_id] = now
        return tokens


@dataclass(slots=True)
class HybridFlowControlPolicy(ExecutionIpcFlowControlPolicy):
    credits: CreditWindowFlowControlPolicy
    tokens: TokenBucketFlowControlPolicy
    requires_ack: bool = True

    def acquire(self, target_id: str, count: int) -> None:
        self.tokens.acquire(target_id, count)
        self.credits.acquire(target_id, count)

    def release(self, target_id: str, count: int) -> None:
        self.credits.release(target_id, count)


def resolve_execution_ipc_flow_control(runtime: dict[str, object]) -> ExecutionIpcFlowControlPolicy:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return CreditWindowFlowControlPolicy(window_size=16)
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return CreditWindowFlowControlPolicy(window_size=16)
    cfg = execution_ipc.get("flow_control", {})
    if not isinstance(cfg, dict):
        return CreditWindowFlowControlPolicy(window_size=16)

    mode = cfg.get("mode", "credits")
    if not isinstance(mode, str) or not mode:
        mode = "credits"
    mode = mode.strip().lower()

    credits_cfg = cfg.get("credits", {})
    if not isinstance(credits_cfg, dict):
        credits_cfg = {}
    window_size = credits_cfg.get("window_size", 16)
    if not isinstance(window_size, int) or window_size <= 0:
        window_size = 16

    bucket_cfg = cfg.get("token_bucket", {})
    if not isinstance(bucket_cfg, dict):
        bucket_cfg = {}
    rate_per_sec = bucket_cfg.get("rate_per_sec", 20000)
    if not isinstance(rate_per_sec, (int, float)) or rate_per_sec <= 0:
        rate_per_sec = 20000
    burst = bucket_cfg.get("burst", rate_per_sec)
    if not isinstance(burst, (int, float)) or burst <= 0:
        burst = rate_per_sec

    credits = CreditWindowFlowControlPolicy(window_size=window_size)
    tokens = TokenBucketFlowControlPolicy(rate_per_sec=float(rate_per_sec), burst=float(burst))

    if mode == "none":
        return NoopFlowControlPolicy()
    if mode == "token_bucket":
        return tokens
    if mode == "hybrid":
        return HybridFlowControlPolicy(credits=credits, tokens=tokens)
    return credits


__all__ = [
    "ExecutionIpcFlowControlPolicy",
    "NoopFlowControlPolicy",
    "CreditWindowFlowControlPolicy",
    "TokenBucketFlowControlPolicy",
    "HybridFlowControlPolicy",
    "resolve_execution_ipc_flow_control",
]
