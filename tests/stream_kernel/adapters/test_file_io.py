from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stream_kernel.adapters.file_io import ByteRecord, FileLineInputSource, FileOutputSink, StreamLine, TextRecord


@dataclass(frozen=True, slots=True)
class _Line:
    seq: int
    payload: bytes


def test_file_line_input_source_reads_in_order(tmp_path: Path) -> None:
    # Framework file source must preserve physical line order and 1-based index.
    path = tmp_path / "input.txt"
    path.write_bytes(b"a\nb\n")
    source = FileLineInputSource(path=path, line_builder=lambda n, t: _Line(n, t))
    assert list(source.read()) == [_Line(1, b"a"), _Line(2, b"b")]


def test_file_line_input_source_preserves_blank_lines(tmp_path: Path) -> None:
    # Blank lines are preserved for deterministic 1:1 stream semantics.
    path = tmp_path / "input.txt"
    path.write_bytes(b"a\n\nb\n")
    source = FileLineInputSource(path=path, line_builder=lambda n, t: _Line(n, t))
    assert list(source.read()) == [_Line(1, b"a"), _Line(2, b""), _Line(3, b"b")]


def test_file_line_input_source_missing_file_raises(tmp_path: Path) -> None:
    # Missing file should fail fast in platform adapter.
    source = FileLineInputSource(path=tmp_path / "missing.txt", line_builder=lambda n, t: _Line(n, t))
    with pytest.raises(FileNotFoundError):
        list(source.read())


def test_transport_models_define_stable_stage_a_contract() -> None:
    # Stage A: transport model is explicit, not implicit in free-form dict payloads.
    assert StreamLine is ByteRecord
    assert ByteRecord(payload=b"x", seq=1, source="ingress").payload == b"x"
    assert TextRecord(text="x", seq=1, source="ingress", encoding="utf-8").text == "x"


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
