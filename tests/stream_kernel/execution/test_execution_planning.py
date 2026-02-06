from __future__ import annotations

# Execution planning rules are described in docs/framework/initial_stage/Execution planning model.md.
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.planning import plan_pools


class EventA:
    pass


class EventB:
    pass


class _NodeSyncOnly:
    # Injected dependency is sync-capable only.
    stream = inject.stream(EventA)


class _NodeAsyncOnly:
    # Injected dependency will be registered as async-capable.
    stream = inject.stream(EventA)


class _NodeMixed:
    # Mixed dependencies should push the node into async pool (Execution planning §8.3).
    stream_a = inject.stream(EventA)
    stream_b = inject.stream(EventB)


def test_plan_pools_defaults_to_sync_for_sync_only_node() -> None:
    # Nodes without async dependencies should stay in the sync pool (§8.1).
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: object(), is_async=False)
    pools = plan_pools({"sync_node": _NodeSyncOnly()}, reg)
    assert pools["sync_node"] == "sync"


def test_plan_pools_assigns_async_for_async_dependency() -> None:
    # Async-capable adapter should drive node into async pool (§8.2).
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: object(), is_async=True)
    pools = plan_pools({"async_node": _NodeAsyncOnly()}, reg)
    assert pools["async_node"] == "async"


def test_plan_pools_prefers_async_for_mixed_dependencies() -> None:
    # Mixed sync/async deps should choose async pool (§8.3).
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: object(), is_async=False)
    reg.register_factory("stream", EventB, lambda: object(), is_async=True)
    pools = plan_pools({"mixed_node": _NodeMixed()}, reg)
    assert pools["mixed_node"] == "async"
