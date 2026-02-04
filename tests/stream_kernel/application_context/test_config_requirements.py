from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.application_context.config_inject import config, ConfigValue
from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("config_requirements_nodes")

    @node(name="a")
    @dataclass(frozen=True, slots=True)
    class A:
        # Required config (no default).
        limit: object = config.value("limits.daily")
        # Optional config (has default).
        tz: object = config.value("global.timezone", default="UTC")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @node(name="b")
    class B:
        # Class attribute also counts as config requirement.
        flag = config.value("flags.enabled")

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    @stage(name="stage_c")
    class CStage:
        # Container field should be discovered for method node.
        rate = config.value("limits.rate")

        @node(name="c")
        def do_c(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.A = A
    mod.B = B
    mod.CStage = CStage
    return mod


def test_config_requirements_listed_per_node() -> None:
    # ApplicationContext should expose required config paths for each node.
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    reqs = ctx.config_requirements()
    assert "a" in reqs
    assert "b" in reqs
    assert "c" in reqs

    a_paths = {req.path for req in reqs["a"]}
    b_paths = {req.path for req in reqs["b"]}
    c_paths = {req.path for req in reqs["c"]}
    assert "limits.daily" in a_paths
    assert "flags.enabled" in b_paths
    assert "limits.rate" in c_paths

    # Defaults should be represented but not required.
    tz_entry = next(req for req in reqs["a"] if req.path == "global.timezone")
    assert isinstance(tz_entry, ConfigValue)
    assert tz_entry.has_default is True


def test_validate_config_requirements_fails_on_missing_required() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    with pytest.raises(ContextBuildError):
        ctx.validate_config_requirements(
            {"flags": {"enabled": True}},
            strict=True,
        )


def test_validate_config_requirements_allows_missing_defaults() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    # Required fields present; optional default is missing and should not fail.
    ctx.validate_config_requirements(
        {
            "limits": {"daily": 5, "rate": 1.5},
            "flags": {"enabled": True},
        },
        strict=True,
    )
