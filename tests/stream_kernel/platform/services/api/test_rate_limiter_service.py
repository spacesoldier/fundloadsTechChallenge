from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.platform.services.api.policy import InMemoryRateLimiterService


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def test_api_lim_01_fixed_window_allows_then_blocks_then_resets() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "fixed_window", "limit": 2, "window_ms": 1000},
        now_fn=clock,
    )

    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False

    clock.now = 1.01
    assert limiter.allow() is True


def test_api_lim_02_sliding_window_counter_deterministic_weighted_quota() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "sliding_window_counter", "limit": 10, "window_ms": 1000},
        now_fn=clock,
    )

    for _ in range(10):
        assert limiter.allow() is True

    clock.now = 1.2
    assert limiter.allow(cost=2) is True
    assert limiter.allow() is False


def test_api_lim_03_token_bucket_refill_and_consume_with_simulated_clock() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiterService(
        limiter_config={
            "kind": "token_bucket",
            "refill_rate_per_sec": 2.0,
            "bucket_capacity": 5,
        },
        now_fn=clock,
    )

    assert limiter.allow(cost=5) is True
    assert limiter.allow() is False

    clock.now = 1.0
    assert limiter.allow(cost=2) is True
    assert limiter.allow() is False

    clock.now = 2.5
    assert limiter.allow(cost=3) is True


def test_api_lim_04_leaky_bucket_drains_deterministically_under_burst() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiterService(
        limiter_config={
            "kind": "leaky_bucket",
            "refill_rate_per_sec": 2.0,
            "bucket_capacity": 5,
        },
        now_fn=clock,
    )

    assert limiter.allow(cost=5) is True
    assert limiter.allow() is False

    clock.now = 1.0
    assert limiter.allow(cost=2) is True
    assert limiter.allow() is False

    clock.now = 3.5
    assert limiter.allow(cost=5) is True


def test_api_lim_05_concurrency_limiter_enforces_cap_and_releases_on_complete() -> None:
    limiter = InMemoryRateLimiterService(
        limiter_config={"kind": "concurrency", "max_in_flight": 2},
    )

    assert limiter.allow(key="k") is True
    assert limiter.allow(key="k") is True
    assert limiter.allow(key="k") is False

    limiter.release(key="k")
    assert limiter.allow(key="k") is True

    limiter.release(key="k", cost=2)
    assert limiter.allow(key="k", cost=2) is True
    assert limiter.allow(key="k") is False
