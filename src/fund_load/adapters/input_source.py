from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from fund_load.domain.messages import RawLine
from fund_load.ports.input_source import InputSource


@dataclass(frozen=True, slots=True)
class FileInputSource(InputSource):
    # File-based InputSource adapter (docs/implementation/ports/InputSource.md).
    path: Path

    def read(self) -> Iterable[RawLine]:
        # File is opened and streamed line-by-line to avoid loading the whole file.
        # In case of external storage (e.g., S3/Redis streams), this is a port boundary.
        with self.path.open("r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle, start=1):
                yield RawLine(line_no=idx, raw_text=line.rstrip("\n"))
