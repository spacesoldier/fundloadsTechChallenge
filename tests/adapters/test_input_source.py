from __future__ import annotations

from pathlib import Path

import pytest

# Input source behavior is provided by framework-owned file adapter factories.
from stream_kernel.adapters.file_io import ByteRecord, StreamLine, TextRecord, ingress_file_source


def test_input_source_reads_lines_in_order(tmp_path: Path) -> None:
    # Text-mode ingress emits TextRecord in physical line order with 1-based sequence.
    path = tmp_path / "input.txt"
    path.write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
    source = ingress_file_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [
        TextRecord(text='{"id":"1"}', seq=1, source="ingress_file", encoding="utf-8"),
        TextRecord(text='{"id":"2"}', seq=2, source="ingress_file", encoding="utf-8"),
    ]


def test_input_source_includes_blank_lines(tmp_path: Path) -> None:
    # Docs allow skipping blanks, but we choose to emit them to preserve 1:1 input/output mapping.
    path = tmp_path / "input.txt"
    path.write_text('{"id":"1"}\n\n{"id":"2"}\n', encoding="utf-8")
    source = ingress_file_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [
        TextRecord(text='{"id":"1"}', seq=1, source="ingress_file", encoding="utf-8"),
        TextRecord(text="", seq=2, source="ingress_file", encoding="utf-8"),
        TextRecord(text='{"id":"2"}', seq=3, source="ingress_file", encoding="utf-8"),
    ]


def test_input_source_preserves_raw_text(tmp_path: Path) -> None:
    # Adapter must preserve decoded text (minus trailing newline) for bridge/parser decisions.
    path = tmp_path / "input.txt"
    path.write_text('  {"id":"1"}  \n', encoding="utf-8")
    source = ingress_file_source({"path": str(path)})
    lines = list(source.read())
    assert lines == [TextRecord(text='  {"id":"1"}  ', seq=1, source="ingress_file", encoding="utf-8")]


def test_input_source_emits_bytes_for_octet_stream_format(tmp_path: Path) -> None:
    # Binary mode emits ByteRecord and does not decode payload.
    path = tmp_path / "input.bin"
    path.write_bytes(b"\xff\x00\n")
    source = ingress_file_source({"path": str(path), "format": "application/octet-stream"})
    lines = list(source.read())
    assert lines == [ByteRecord(payload=b"\xff\x00", seq=1, source="ingress_file")]


def test_input_source_emits_text_record_for_text_plain_format(tmp_path: Path) -> None:
    # Explicit text/plain mode emits TextRecord.
    path = tmp_path / "input.txt"
    path.write_text("hello\n", encoding="utf-8")
    source = ingress_file_source({"path": str(path), "format": "text/plain"})
    lines = list(source.read())
    assert lines == [TextRecord(text="hello", seq=1, source="ingress_file", encoding="utf-8")]


def test_input_source_replace_decode_errors_fallback_for_text_format(tmp_path: Path) -> None:
    # Decode fallback is adapter-level behavior configured in settings.
    path = tmp_path / "input.bin"
    path.write_bytes(b"\xff\n")
    source = ingress_file_source(
        {"path": str(path), "format": "text/plain", "encoding": "utf-8", "decode_errors": "replace"}
    )
    lines = list(source.read())
    assert lines == [TextRecord(text="\ufffd", seq=1, source="ingress_file", encoding="utf-8")]


def test_input_source_rejects_unknown_decode_errors_policy(tmp_path: Path) -> None:
    # Unsupported decode policy must fail fast during adapter construction.
    path = tmp_path / "input.txt"
    path.write_text("hello\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ingress_file_source({"path": str(path), "decode_errors": "skip"})


def test_streamline_is_backward_compatible_alias_for_byterecord() -> None:
    # Stage A: compatibility alias is kept while callers migrate to ByteRecord.
    assert StreamLine is ByteRecord


def test_input_source_missing_file_raises() -> None:
    # Missing file should fail fast per InputSource spec.
    source = ingress_file_source({"path": str(Path("nope.txt"))})
    with pytest.raises(FileNotFoundError):
        list(source.read())
