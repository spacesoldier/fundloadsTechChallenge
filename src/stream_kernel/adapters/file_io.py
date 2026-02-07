from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True, slots=True)
class FileLineInputSource:
    # Platform file source adapter: reads text file line-by-line and delegates message construction.
    path: Path
    line_builder: Callable[[int, str], object]

    def read(self) -> Iterable[object]:
        # Stream physical file order deterministically (1-based line numbers).
        with self.path.open("r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle, start=1):
                yield self.line_builder(idx, line.rstrip("\n"))


@dataclass
class FileOutputSink:
    # Platform file sink adapter: writes newline-delimited output, optionally atomically.
    path: Path
    atomic_replace: bool = False
    _handle: TextIO | None = field(default=None, init=False, repr=False)
    _temp_path: Path | None = field(default=None, init=False, repr=False)

    def write_line(self, line: str) -> None:
        # Open lazily so construction itself does not touch filesystem.
        if self._handle is None:
            self._open()
        assert self._handle is not None
        self._handle.write(line + "\n")

    def close(self) -> None:
        # Close is idempotent to simplify runner shutdown paths.
        if self._handle is None:
            return
        self._handle.flush()
        self._handle.close()
        self._handle = None

        if self.atomic_replace and self._temp_path is not None:
            self._temp_path.replace(self.path)
            self._temp_path = None

    def _open(self) -> None:
        if self.atomic_replace:
            self._temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            self._handle = self._temp_path.open("w", encoding="utf-8")
        else:
            self._handle = self.path.open("w", encoding="utf-8")

