from __future__ import annotations

from collections.abc import Sequence

from stream_kernel.app import run


def main(argv: Sequence[str] | None = None) -> int:
    # Single-line entrypoint delegating to framework runtime.
    return run(list(argv) if argv is not None else None)
