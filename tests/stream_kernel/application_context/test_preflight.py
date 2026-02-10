from __future__ import annotations

import types
from dataclasses import dataclass

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError
from stream_kernel.kernel.dag import NodeContract
from stream_kernel.kernel.node import node


class RawToken:
    pass


class MidToken:
    pass


class OutToken:
    pass


def _ok_module() -> types.ModuleType:
    mod = types.ModuleType("preflight_ok")

    @node(name="source", consumes=[], emits=[RawToken])
    def source(msg: object, ctx: object | None) -> list[RawToken]:
        return [RawToken()]

    @node(name="transform", consumes=[RawToken], emits=[MidToken])
    @dataclass(frozen=True, slots=True)
    class Transform:
        def __call__(self, msg: RawToken, ctx: object | None) -> list[MidToken]:
            return [MidToken()]

    @node(name="sink", consumes=[MidToken], emits=[OutToken])
    def sink(msg: MidToken, ctx: object | None) -> list[OutToken]:
        return [OutToken()]

    mod.source = source
    mod.Transform = Transform
    mod.sink = sink
    return mod


def _overlap_module() -> types.ModuleType:
    mod = types.ModuleType("preflight_overlap")

    @node(name="looping", consumes=[RawToken], emits=[RawToken])
    def looping(msg: RawToken, ctx: object | None) -> list[RawToken]:
        return [msg]

    mod.looping = looping
    return mod


def test_preflight_passes_for_valid_graph() -> None:
    # Preflight should pass when DAG is valid and no self-loop contracts exist.
    ctx = ApplicationContext()
    ctx.discover([_ok_module()])
    ctx.preflight(strict=True)


def test_preflight_rejects_consumes_emits_overlap_in_strict_mode() -> None:
    # Nodes that consume and emit the same token require explicit policy and fail strict preflight.
    ctx = ApplicationContext()
    ctx.discover([_overlap_module()])
    with pytest.raises(ContextBuildError):
        ctx.preflight(strict=True)


def test_preflight_allows_overlap_in_non_strict_mode() -> None:
    # Non-strict preflight keeps compatibility mode for migration.
    ctx = ApplicationContext()
    ctx.discover([_overlap_module()])
    ctx.preflight(strict=False)


def test_preflight_accepts_missing_provider_when_adapter_contract_emits_token() -> None:
    # Graph-native source adapters close open DAG ends without external token shims.
    mod = types.ModuleType("preflight_adapter_source")

    @node(name="transform", consumes=[RawToken], emits=[MidToken])
    def transform(msg: RawToken, ctx: object | None) -> list[MidToken]:
        return [MidToken()]

    mod.transform = transform
    ctx = ApplicationContext()
    ctx.discover([mod])
    ctx.preflight(
        strict=True,
        extra_contracts=[NodeContract(name="input_source", consumes=[], emits=[RawToken], external=True)],
    )
