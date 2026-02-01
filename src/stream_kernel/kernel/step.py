from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from stream_kernel.kernel.context import Context

# Protocol variance: inputs are contravariant, outputs are covariant (mypy requirement).
TIn = TypeVar("TIn", contravariant=True)
TOut = TypeVar("TOut", covariant=True)


class Step(Protocol, Generic[TIn, TOut]):
    # Step contract is (msg, ctx) -> Iterable[out], per kernel Step Contract spec.
    def __call__(self, msg: TIn, ctx: Context | None) -> Iterable[TOut]:
        raise NotImplementedError("Step protocol has no implementation")


@dataclass(frozen=True, slots=True)
class Map(Generic[TIn, TOut]):
    # Map applies a transformation and emits exactly one output.
    fn: Callable[[TIn, Context | None], TOut]

    def __call__(self, msg: TIn, ctx: Context | None) -> Iterable[TOut]:
        return [self.fn(msg, ctx)]


@dataclass(frozen=True, slots=True)
class Filter(Generic[TIn]):
    # Filter drops or passes a message based on predicate.
    pred: Callable[[TIn, Context | None], bool]

    def __call__(self, msg: TIn, ctx: Context | None) -> Iterable[TIn]:
        return [msg] if self.pred(msg, ctx) else []


@dataclass(frozen=True, slots=True)
class Tap(Generic[TIn]):
    # Tap performs a side-effect and returns the original message.
    fn: Callable[[TIn, Context | None], None]

    def __call__(self, msg: TIn, ctx: Context | None) -> Iterable[TIn]:
        self.fn(msg, ctx)
        return [msg]
