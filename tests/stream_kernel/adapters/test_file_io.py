from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stream_kernel.adapters.file_io import FileLineInputSource, FileOutputSink


@dataclass(frozen=True, slots=True)
class _Line:
    line_no: int
    raw_text: str


def test_file_line_input_source_reads_in_order(tmp_path: Path) -> None:
    # Framework file source must preserve physical line order and 1-based numbering.
    path = tmp_path / "input.txt"
    path.write_text("a\nb\n", encoding="utf-8")
    source = FileLineInputSource(path=path, line_builder=lambda n, t: _Line(n, t))
    assert list(source.read()) == [_Line(1, "a"), _Line(2, "b")]


def test_file_line_input_source_preserves_blank_lines(tmp_path: Path) -> None:
    # Blank lines are preserved for deterministic 1:1 stream semantics.
    path = tmp_path / "input.txt"
    path.write_text("a\n\nb\n", encoding="utf-8")
    source = FileLineInputSource(path=path, line_builder=lambda n, t: _Line(n, t))
    assert list(source.read()) == [_Line(1, "a"), _Line(2, ""), _Line(3, "b")]


def test_file_line_input_source_missing_file_raises(tmp_path: Path) -> None:
    # Missing file should fail fast in platform adapter.
    source = FileLineInputSource(path=tmp_path / "missing.txt", line_builder=lambda n, t: _Line(n, t))
    with pytest.raises(FileNotFoundError):
        list(source.read())


def test_file_output_sink_writes_lines(tmp_path: Path) -> None:
    # Framework file sink writes one line per call in order.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path=path)
    sink.write_line("x")
    sink.write_line("y")
    sink.close()
    assert path.read_text(encoding="utf-8") == "x\ny\n"


def test_file_output_sink_atomic_replace(tmp_path: Path) -> None:
    # Atomic mode writes to temp and replaces final target on close.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path=path, atomic_replace=True)
    sink.write_line("x")
    sink.close()
    assert path.exists()


def test_file_output_sink_close_is_idempotent(tmp_path: Path) -> None:
    # Calling close twice should be safe.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path=path)
    sink.write_line("x")
    sink.close()
    sink.close()
    assert path.read_text(encoding="utf-8") == "x\n"

