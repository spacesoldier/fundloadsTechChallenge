from __future__ import annotations

from pathlib import Path
from typing import Any

from fund_load.domain.messages import RawLine
from fund_load.usecases.messages import OutputLine
from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.file_io import FileLineInputSource, FileOutputSink


@adapter(
    name="input_source",
    kind="file.line_source",
    consumes=[],
    emits=[RawLine],
    binds=[("stream", FileLineInputSource)],
)
def file_input_source(settings: dict[str, Any]) -> FileLineInputSource:
    # Project mapping stays local: file line -> RawLine model.
    return FileLineInputSource(
        path=Path(settings["path"]),
        line_builder=lambda idx, text: RawLine(line_no=idx, raw_text=text),
    )


@adapter(
    name="output_sink",
    kind="file.line_sink",
    consumes=[OutputLine],
    emits=[],
    binds=[("stream", FileOutputSink)],
)
def file_output_sink(settings: dict[str, Any]) -> FileOutputSink:
    return FileOutputSink(
        path=Path(settings["path"]),
        atomic_replace=bool(settings.get("atomic_replace", False)),
    )

