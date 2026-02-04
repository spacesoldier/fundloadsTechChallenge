from __future__ import annotations

import types
from dataclasses import dataclass

from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.config_inject import config
from stream_kernel.kernel.node import node


def _make_module_basic() -> types.ModuleType:
    mod = types.ModuleType("config_slice_nodes_basic")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        # Reads from node slice by default.
        limit: object = config.value("limit")
        # Reads from global scope explicitly.
        tz: object = config.value("global.timezone")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b")
    @dataclass(frozen=True, slots=True)
    class B:
        limit: object = config.value("limit")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    mod.B = B
    return mod


def _make_module_with_cross_access() -> types.ModuleType:
    mod = types.ModuleType("config_slice_nodes_cross")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        # Explicit access to global.nodes in non-strict mode.
        other_limit: object = config.value("global.nodes.b.limit")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    return mod


def test_config_slice_isolated_per_node() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module_basic()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a", "b"],
        wiring={
            # Strict mode keeps node slices isolated unless explicitly global.
            "strict": True,
            "config": {
                "nodes": {
                    "a": {"limit": 5},
                    "b": {"limit": 9},
                },
                "global": {"timezone": "UTC"},
            }
        },
    )

    a = scenario.steps[0].step
    b = scenario.steps[1].step
    # Node slices are isolated: each node only sees its own section.
    assert a.limit == 5
    assert b.limit == 9
    # Explicit global access still works in strict mode.
    assert a.tz == "UTC"


def test_global_scope_fallback_when_no_node_slice() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module_basic()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={
            "strict": False,
            "config": {
                "global": {"limit": 7, "timezone": "UTC"},
            }
        },
    )

    a = scenario.steps[0].step
    # If no node slice exists, global scope can be used as fallback.
    assert a.limit == 7
    assert a.tz == "UTC"


def test_global_nodes_access_allowed_in_non_strict_mode() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module_with_cross_access()])

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["a"],
        wiring={
            "strict": False,
            "config": {
                "nodes": {"b": {"limit": 9}},
                "global": {"timezone": "UTC"},
            },
        },
    )

    a = scenario.steps[0].step
    # Non-strict mode allows cross-node reads via global.nodes.* fallback.
    assert a.other_limit == 9


def test_global_nodes_access_rejected_in_strict_mode() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module_with_cross_access()])

    try:
        ctx.build_scenario(
            scenario_id="s1",
            step_names=["a"],
            wiring={
                "strict": True,
                "config": {
                    "nodes": {"b": {"limit": 9}},
                    "global": {"timezone": "UTC"},
                },
            },
        )
    except Exception as exc:  # ContextBuildError is expected here.
        # Strict mode rejects cross-node access even if the data exists elsewhere.
        assert "global.nodes.b.limit" in str(exc) or "nodes.b.limit" in str(exc)
    else:
        raise AssertionError("strict mode must reject global.nodes.* access")
