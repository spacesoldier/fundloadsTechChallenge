from __future__ import annotations

from pathlib import Path

import pytest

# InputSource adapter behavior is specified in docs/implementation/ports/InputSource.md.
from fund_load.adapters.io import file_input_source
from fund_load.domain.messages import RawLine


def test_input_source_reads_lines_in_order(tmp_path: Path) -> None:
    # File adapter must emit RawLine in physical line order with 1-based line_no.
    path = tmp_path / "input.txt"
    path.write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
    source = file_input_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [
        RawLine(line_no=1, raw_text='{"id":"1"}'),
        RawLine(line_no=2, raw_text='{"id":"2"}'),
    ]


def test_input_source_includes_blank_lines(tmp_path: Path) -> None:
    # Docs allow skipping blanks, but we choose to emit them to preserve 1:1 input/output mapping.
    path = tmp_path / "input.txt"
    path.write_text('{"id":"1"}\n\n{"id":"2"}\n', encoding="utf-8")
    source = file_input_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [
        RawLine(line_no=1, raw_text='{"id":"1"}'),
        RawLine(line_no=2, raw_text=""),
        RawLine(line_no=3, raw_text='{"id":"2"}'),
    ]


def test_input_source_preserves_raw_text(tmp_path: Path) -> None:
    # Adapter must preserve raw text (minus trailing newline) for parse step to decide validity.
    path = tmp_path / "input.txt"
    path.write_text('  {"id":"1"}  \n', encoding="utf-8")
    source = file_input_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [RawLine(line_no=1, raw_text='  {"id":"1"}  ')]


def test_input_source_missing_file_raises() -> None:
    # Missing file should fail fast per InputSource spec.
    source = file_input_source({"path": str(Path("nope.txt"))})
    with pytest.raises(FileNotFoundError):
        list(source.read())
