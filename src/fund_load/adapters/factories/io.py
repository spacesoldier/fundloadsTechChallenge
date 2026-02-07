from __future__ import annotations

from pathlib import Path
from typing import Any

from fund_load.domain.messages import RawLine
from fund_load.ports.input_source import InputSource
from fund_load.ports.output_sink import OutputSink
from fund_load.usecases.messages import OutputLine
from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.file_io import FileLineInputSource, FileOutputSink


@adapter(
    name="input_source",
    kind="file.line_source",
    consumes=[],
    emits=[RawLine],
    binds=[("stream", InputSource)],
)
def file_input_source(settings: dict[str, Any]) -> FileLineInputSource:
    # Factory for file-based input source.
    return FileLineInputSource(
        path=Path(settings["path"]),
        # Domain mapping stays in project: each physical line becomes a RawLine model.
        line_builder=lambda idx, text: RawLine(line_no=idx, raw_text=text),
    )


@adapter(
    name="output_sink",
    kind="file.line_sink",
    consumes=[OutputLine],
    emits=[],
    binds=[("stream", OutputSink)],
)
def file_output_sink(settings: dict[str, Any]) -> FileOutputSink:
    # Factory for file-based output sink.
    return FileOutputSink(
        path=Path(settings["path"]),
        atomic_replace=bool(settings.get("atomic_replace", False)),
    )
