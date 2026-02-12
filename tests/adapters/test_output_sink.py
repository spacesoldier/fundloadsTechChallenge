from __future__ import annotations

from pathlib import Path

# Output sink behavior is provided by framework-owned file adapter factories.
from stream_kernel.adapters.file_io import SinkLine, egress_file_sink


def test_output_sink_writes_ndjson_lines(tmp_path: Path) -> None:
    # Adapter writes one JSON object per line, preserving order.
    path = tmp_path / "out.txt"
    sink = egress_file_sink({"path": str(path)})
    sink.consume(SinkLine(text='{"id":"1"}'))
    sink.consume(SinkLine(text='{"id":"2"}'))
    sink.close()
    assert path.read_text(encoding="utf-8") == '{"id":"1"}\n{"id":"2"}\n'


def test_output_sink_atomic_replace(tmp_path: Path) -> None:
    # Atomic replace writes to temp and then renames to final path (OutputSink spec).
    path = tmp_path / "out.txt"
    sink = egress_file_sink({"path": str(path), "atomic_replace": True})
    sink.consume(SinkLine(text='{"id":"1"}'))
    sink.close()
    assert path.exists()


def test_output_sink_close_is_idempotent(tmp_path: Path) -> None:
    # Closing twice should not raise and should not corrupt output.
    path = tmp_path / "out.txt"
    sink = egress_file_sink({"path": str(path)})
    sink.consume(SinkLine(text='{"id":"1"}'))
    sink.close()
    sink.close()
    assert path.read_text(encoding="utf-8") == '{"id":"1"}\n'


def test_output_sink_honors_explicit_encoding(tmp_path: Path) -> None:
    # Sink encoding comes from adapter settings; bridge/business code should not hardcode it.
    path = tmp_path / "out.txt"
    sink = egress_file_sink({"path": str(path), "encoding": "utf-16-le"})
    sink.consume(SinkLine(text="alpha"))
    sink.close()
    assert path.read_text(encoding="utf-16-le") == "alpha\n"


def test_output_sink_accepts_supported_text_formats(tmp_path: Path) -> None:
    # Sink supports line-oriented text formats in the same adapter implementation.
    path_jsonl = tmp_path / "out-jsonl.txt"
    sink_jsonl = egress_file_sink({"path": str(path_jsonl), "format": "text/jsonl"})
    sink_jsonl.consume(SinkLine(text='{"id":"1"}'))
    sink_jsonl.close()
    assert path_jsonl.read_text(encoding="utf-8") == '{"id":"1"}\n'

    path_plain = tmp_path / "out-plain.txt"
    sink_plain = egress_file_sink({"path": str(path_plain), "format": "text/plain"})
    sink_plain.consume(SinkLine(text="line"))
    sink_plain.close()
    assert path_plain.read_text(encoding="utf-8") == "line\n"


def test_output_sink_rejects_non_text_format(tmp_path: Path) -> None:
    # Binary ingress format is not valid for a line sink.
    path = tmp_path / "out.txt"
    try:
        egress_file_sink({"path": str(path), "format": "application/octet-stream"})
    except ValueError as exc:
        assert "supports only text/jsonl or text/plain" in str(exc)
    else:
        raise AssertionError("expected ValueError for unsupported sink format")
