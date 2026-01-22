from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from fund_load.ports.output_sink import OutputSink


@dataclass
class FileOutputSink(OutputSink):
    # File-based OutputSink adapter (docs/implementation/ports/OutputSink.md).
    path: Path
    atomic_replace: bool = False
    _handle: TextIO | None = field(default=None, init=False, repr=False)
    _temp_path: Path | None = field(default=None, init=False, repr=False)

    def write_line(self, line: str) -> None:
        # Open lazily so construction does not touch filesystem.
        if self._handle is None:
            self._open()
        assert self._handle is not None
        self._handle.write(line + "\n")

    def close(self) -> None:
        # Close is idempotent; safe to call multiple times.
        if self._handle is None:
            return
        self._handle.flush()
        self._handle.close()
        self._handle = None

        if self.atomic_replace and self._temp_path is not None:
            # Atomic replace commits the temp file to the final path.
            self._temp_path.replace(self.path)
            self._temp_path = None

    def _open(self) -> None:
        # If atomic_replace is enabled, write to a temp file first.
        if self.atomic_replace:
            self._temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            self._handle = self._temp_path.open("w", encoding="utf-8")
        else:
            self._handle = self.path.open("w", encoding="utf-8")
