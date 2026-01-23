from __future__ import annotations

from collections.abc import Sequence

from fund_load.app.cli import run


def main(argv: Sequence[str] | None = None) -> int:
    # Delegate to CLI runner for a single, testable entrypoint (Composition Root spec §6.1).
    return run(argv)
