from __future__ import annotations

import time
from threading import Event, Thread

from stream_kernel.execution.transport.ipc.flow_control import (
    CreditWindowFlowControlPolicy,
    HybridFlowControlPolicy,
    TokenBucketFlowControlPolicy,
)


def test_credit_flow_control_blocks_until_release() -> None:
    policy = CreditWindowFlowControlPolicy(window_size=1)
    policy.acquire("alpha", 1)

    started = Event()
    released = Event()

    def _worker() -> None:
        started.set()
        policy.acquire("alpha", 1)
        released.set()

    thread = Thread(target=_worker, daemon=True)
    thread.start()
    assert started.wait(timeout=0.2)
    assert not released.wait(timeout=0.05)
    policy.release("alpha", 1)
    assert released.wait(timeout=0.2)


def test_token_bucket_flow_control_throttles_rate() -> None:
    policy = TokenBucketFlowControlPolicy(rate_per_sec=10, burst=1)
    policy.acquire("alpha", 1)
    start = time.monotonic()
    policy.acquire("alpha", 1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08


def test_hybrid_flow_control_enforces_credits() -> None:
    policy = HybridFlowControlPolicy(
        credits=CreditWindowFlowControlPolicy(window_size=1),
        tokens=TokenBucketFlowControlPolicy(rate_per_sec=1000, burst=1000),
    )
    policy.acquire("alpha", 1)

    started = Event()
    released = Event()

    def _worker() -> None:
        started.set()
        policy.acquire("alpha", 1)
        released.set()

    thread = Thread(target=_worker, daemon=True)
    thread.start()
    assert started.wait(timeout=0.2)
    assert not released.wait(timeout=0.05)
    policy.release("alpha", 1)
    assert released.wait(timeout=0.2)
