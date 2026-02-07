from __future__ import annotations

from stream_kernel.adapters.contracts import adapter, get_adapter_meta


class _InToken:
    pass


class _OutToken:
    pass


class _PortType:
    pass


@adapter(
    name="reader",
    kind="file.line_reader",
    consumes=[_InToken],
    emits=[_OutToken],
    binds=[("stream", _PortType)],
)
def _factory(settings: dict[str, object]) -> object:
    # Adapter factory contract is declared via decorator metadata.
    return object()


def test_adapter_decorator_attaches_contract_metadata() -> None:
    # Framework must read strongly-typed contracts from decorator, not from YAML strings.
    meta = get_adapter_meta(_factory)
    assert meta is not None
    assert meta.name == "reader"
    assert meta.kind == "file.line_reader"
    assert list(meta.consumes) == [_InToken]
    assert list(meta.emits) == [_OutToken]
    assert list(meta.binds) == [("stream", _PortType)]


def test_get_adapter_meta_returns_none_for_plain_callable() -> None:
    # Non-decorated callables are valid factories but provide no routing contracts.
    meta = get_adapter_meta(lambda settings: object())
    assert meta is None
