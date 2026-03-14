from __future__ import annotations

import os
import time
from dataclasses import dataclass
from threading import Condition


class ExecutionIpcFlowControlPolicy:
    requires_ack: bool = False

    def acquire(self, target_id: str, count: int) -> None:
        raise NotImplementedError

    def try_acquire(self, target_id: str, count: int) -> bool:
        # Backward-compatible default: blocking acquire then success.
        self.acquire(target_id, count)
        return True

    def release(self, target_id: str, count: int) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class NoopFlowControlPolicy(ExecutionIpcFlowControlPolicy):
    requires_ack: bool = False

    def acquire(self, target_id: str, count: int) -> None:
        _ = target_id, count

    def try_acquire(self, target_id: str, count: int) -> bool:
        _ = target_id, count
        return True

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

    def try_acquire(self, target_id: str, count: int) -> bool:
        if count <= 0:
            return True
        with self._condition:
            if self._inflight.get(target_id, 0) + count > self.window_size:
                return False
            self._inflight[target_id] = self._inflight.get(target_id, 0) + count
            return True

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

    def try_acquire(self, target_id: str, count: int) -> bool:
        if count <= 0:
            return True
        with self._condition:
            tokens = self._refill(target_id)
            if tokens < count:
                return False
            self._tokens[target_id] = tokens - count
            return True

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

    def try_acquire(self, target_id: str, count: int) -> bool:
        if not self.credits.try_acquire(target_id, count):
            return False
        if self.tokens.try_acquire(target_id, count):
            return True
        self.credits.release(target_id, count)
        return False

    def release(self, target_id: str, count: int) -> None:
        self.credits.release(target_id, count)


def resolve_execution_ipc_flow_control(runtime: dict[str, object]) -> ExecutionIpcFlowControlPolicy:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return NoopFlowControlPolicy()
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return NoopFlowControlPolicy()
    cfg = execution_ipc.get("flow_control", {})
    if not isinstance(cfg, dict):
        return NoopFlowControlPolicy()
    cfg = _resolve_runtime_flow_control_config(runtime=runtime, cfg=cfg)

    mode = cfg.get("mode", "none")
    if not isinstance(mode, str) or not mode:
        mode = "none"
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


def _resolve_runtime_flow_control_config(
    *,
    runtime: dict[str, object],
    cfg: dict[str, object],
) -> dict[str, object]:
    resolved = dict(cfg)
    process_group = _runtime_process_group(runtime)
    if not isinstance(process_group, str) or not process_group:
        return resolved
    resolved, has_group_override = _apply_per_group_override(resolved, process_group=process_group)
    if has_group_override:
        return resolved
    return _apply_auto_group_credit_window(
        runtime=runtime,
        cfg=resolved,
        process_group=process_group,
    )


def _runtime_process_group(runtime: dict[str, object]) -> str | None:
    if isinstance(runtime, dict):
        value = runtime.get("__process_group")
        if isinstance(value, str) and value:
            return value
    env_value = os.environ.get("STREAM_KERNEL_PROCESS_GROUP")
    if isinstance(env_value, str) and env_value:
        return env_value
    return None


def _apply_per_group_override(
    cfg: dict[str, object],
    *,
    process_group: str,
) -> tuple[dict[str, object], bool]:
    per_group = cfg.get("per_group")
    if not isinstance(per_group, dict):
        return (cfg, False)
    override = per_group.get(process_group)
    if not isinstance(override, dict):
        return (cfg, False)

    merged = dict(cfg)
    mode = override.get("mode")
    if isinstance(mode, str) and mode:
        merged["mode"] = mode

    merged_credits = dict(merged.get("credits")) if isinstance(merged.get("credits"), dict) else {}
    override_credits = override.get("credits")
    if isinstance(override_credits, dict):
        merged_credits.update(override_credits)
    if merged_credits:
        merged["credits"] = merged_credits

    merged_bucket = (
        dict(merged.get("token_bucket")) if isinstance(merged.get("token_bucket"), dict) else {}
    )
    override_bucket = override.get("token_bucket")
    if isinstance(override_bucket, dict):
        merged_bucket.update(override_bucket)
    if merged_bucket:
        merged["token_bucket"] = merged_bucket

    return (merged, True)


def _apply_auto_group_credit_window(
    *,
    runtime: dict[str, object],
    cfg: dict[str, object],
    process_group: str,
) -> dict[str, object]:
    credits_cfg = cfg.get("credits")
    if not isinstance(credits_cfg, dict):
        return cfg
    window_size = credits_cfg.get("window_size")
    if not isinstance(window_size, int) or window_size <= 0:
        return cfg
    weights = _group_credit_weights(runtime)
    if process_group not in weights:
        return cfg
    allocated = _allocate_group_credit_windows(window_size=window_size, weights=weights)
    group_window = allocated.get(process_group)
    if not isinstance(group_window, int) or group_window <= 0:
        return cfg
    merged = dict(cfg)
    merged_credits = dict(credits_cfg)
    merged_credits["window_size"] = group_window
    merged["credits"] = merged_credits
    return merged


def _group_credit_weights(runtime: dict[str, object]) -> dict[str, int]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return {}
    weights: dict[str, int] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        workers = item.get("workers", 1)
        if not isinstance(workers, int) or workers <= 0:
            workers = 1
        raw_nodes = item.get("nodes", [])
        node_count = 0
        if isinstance(raw_nodes, list):
            node_count = sum(1 for node_name in raw_nodes if isinstance(node_name, str) and node_name)
        weight = max(1, workers * max(1, node_count))
        weights[name] = weight
    if _observability_service_worker_enabled(runtime):
        weights.setdefault("system.observability", 1)
    return weights


def _observability_service_worker_enabled(runtime: dict[str, object]) -> bool:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    for key in ("service_process", "service_worker"):
        section = observability.get(key)
        if not isinstance(section, dict):
            continue
        enabled = section.get("enabled", True)
        if isinstance(enabled, bool):
            if enabled:
                return True
            continue
        return True
    return False


def _allocate_group_credit_windows(
    *,
    window_size: int,
    weights: dict[str, int],
) -> dict[str, int]:
    if window_size <= 0 or not weights:
        return {}
    positive = {name: max(1, int(weight)) for name, weight in weights.items() if isinstance(name, str) and name}
    if not positive:
        return {}
    total_weight = sum(positive.values())
    if total_weight <= 0:
        return {}

    exact: dict[str, float] = {
        name: (window_size * weight) / total_weight for name, weight in positive.items()
    }
    allocated: dict[str, int] = {name: max(1, int(value)) for name, value in exact.items()}
    current_total = sum(allocated.values())

    if current_total > window_size:
        # Reduce from largest allocations first while preserving a minimum of 1.
        ordered = sorted(allocated, key=lambda item: allocated[item], reverse=True)
        overflow = current_total - window_size
        for name in ordered:
            if overflow <= 0:
                break
            removable = max(0, allocated[name] - 1)
            if removable <= 0:
                continue
            delta = min(removable, overflow)
            allocated[name] -= delta
            overflow -= delta
    elif current_total < window_size:
        # Distribute remainder by largest fractional part.
        remainder = window_size - current_total
        ordered = sorted(
            exact,
            key=lambda item: (exact[item] - int(exact[item]), exact[item]),
            reverse=True,
        )
        if not ordered:
            ordered = list(allocated.keys())
        idx = 0
        while remainder > 0 and ordered:
            name = ordered[idx % len(ordered)]
            allocated[name] += 1
            remainder -= 1
            idx += 1
    return allocated


__all__ = [
    "ExecutionIpcFlowControlPolicy",
    "NoopFlowControlPolicy",
    "CreditWindowFlowControlPolicy",
    "TokenBucketFlowControlPolicy",
    "HybridFlowControlPolicy",
    "resolve_execution_ipc_flow_control",
]
