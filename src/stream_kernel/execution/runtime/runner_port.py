from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RunnerPort(Protocol):
    # Runner interface for execution engines (Execution runtime integration).
    def run(self) -> None:
        # Implementations execute until their work source is exhausted.
        raise NotImplementedError("RunnerPort.run must be implemented")
