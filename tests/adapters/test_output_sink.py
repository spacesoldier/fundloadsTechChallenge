from __future__ import annotations

from pathlib import Path

# OutputSink adapter behavior is specified in docs/implementation/ports/OutputSink.md.
from fund_load.adapters.output_sink import FileOutputSink


def test_output_sink_writes_ndjson_lines(tmp_path: Path) -> None:
    # Adapter writes one JSON object per line, preserving order.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path)
    sink.write_line('{"id":"1"}')
    sink.write_line('{"id":"2"}')
    sink.close()
    assert path.read_text(encoding="utf-8") == '{"id":"1"}\n{"id":"2"}\n'


def test_output_sink_atomic_replace(tmp_path: Path) -> None:
    # Atomic replace writes to temp and then renames to final path (OutputSink spec).
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path, atomic_replace=True)
    sink.write_line('{"id":"1"}')
    sink.close()
    assert path.exists()


def test_output_sink_close_is_idempotent(tmp_path: Path) -> None:
    # Closing twice should not raise and should not corrupt output.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path)
    sink.write_line('{"id":"1"}')
    sink.close()
    sink.close()
    assert path.read_text(encoding="utf-8") == '{"id":"1"}\n'
