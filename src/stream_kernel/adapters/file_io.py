from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from stream_kernel.adapters.contracts import adapter


@dataclass(frozen=True, slots=True)
class ByteRecord:
    # Generic binary transport payload emitted by ingress adapters.
    payload: bytes
    seq: int | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class TextRecord:
    # Generic text transport payload for adapters that decode at boundary.
    text: str
    seq: int | None = None
    source: str = ""
    encoding: str = "utf-8"


# Backward-compatibility alias for existing callers.
StreamLine = ByteRecord


@dataclass(frozen=True, slots=True)
class SinkLine:
    # Generic line-oriented sink payload consumed by file egress adapters.
    text: str
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class FileLineInputSource:
    # Platform file source adapter: reads file in binary mode line-by-line and delegates message construction.
    path: Path
    line_builder: Callable[[int, bytes], object]

    def read(self) -> Iterable[object]:
        # Stream physical file order deterministically (1-based record index).
        with self.path.open("rb") as handle:
            for idx, line in enumerate(handle, start=1):
                yield self.line_builder(idx, line.rstrip(b"\n"))


@dataclass
class FileOutputSink:
    # Platform file sink adapter: writes newline-delimited output, optionally atomically.
    path: Path
    encoding: str = "utf-8"
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
            self._handle = self._temp_path.open("w", encoding=self.encoding)
        else:
            self._handle = self.path.open("w", encoding=self.encoding)


@dataclass
class SinkLineFileSink:
    # Framework sink wrapper: consumes SinkLine and delegates persistence to FileOutputSink.
    sink: FileOutputSink

    def consume(self, payload: SinkLine) -> None:
        self.sink.write_line(payload.text)

    def close(self) -> None:
        self.sink.close()


@adapter(
    name="ingress_file",
    kind="file.line_source",
    consumes=[],
    emits=[ByteRecord, TextRecord],
    binds=[("stream", FileLineInputSource)],
)
def ingress_file_source(settings: dict[str, object]) -> FileLineInputSource:
    # Framework-owned file ingress adapter: maps physical file records to text/binary transport payloads.
    source = str(settings.get("source", "ingress_file"))
    fmt = str(settings.get("format", "text/jsonl"))
    encoding = str(settings.get("encoding", "utf-8"))
    decode_errors = str(settings.get("decode_errors", "strict"))
    if decode_errors not in {"strict", "replace"}:
        raise ValueError("ingress_file.settings.decode_errors must be one of: strict, replace")
    line_builder: Callable[[int, bytes], object]
    if fmt == "application/octet-stream":
        line_builder = lambda idx, payload: ByteRecord(payload=payload, seq=idx, source=source)
    else:
        line_builder = lambda idx, payload: TextRecord(
            text=payload.decode(encoding, errors=decode_errors),
            seq=idx,
            source=source,
            encoding=encoding,
        )
    return FileLineInputSource(
        path=Path(str(settings["path"])),
        line_builder=line_builder,
    )


@adapter(
    name="source",
    kind="file.line_source",
    consumes=[],
    emits=[ByteRecord, TextRecord],
    binds=[("stream", FileLineInputSource)],
)
def source_file_source(settings: dict[str, object]) -> FileLineInputSource:
    # Alias for generic role naming: `source` instead of `ingress_file`.
    return ingress_file_source(settings)


@adapter(
    name="egress_file",
    kind="file.line_sink",
    consumes=[SinkLine],
    emits=[],
    binds=[("stream", FileOutputSink)],
)
def egress_file_sink(settings: dict[str, object]) -> SinkLineFileSink:
    # Framework-owned file egress adapter: persists SinkLine payloads as NDJSON/text lines.
    fmt = settings.get("format", "text/jsonl")
    if not isinstance(fmt, str) or not fmt:
        raise ValueError("egress_file.settings.format must be a non-empty string")
    if fmt not in {"text/jsonl", "text/plain"}:
        raise ValueError("egress_file sink supports only text/jsonl or text/plain formats")

    encoding = settings.get("encoding", "utf-8")
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("egress_file.settings.encoding must be a non-empty string")

    sink = FileOutputSink(
        path=Path(str(settings["path"])),
        encoding=encoding,
        atomic_replace=bool(settings.get("atomic_replace", False)),
    )
    return SinkLineFileSink(sink=sink)


@adapter(
    name="sink",
    kind="file.line_sink",
    consumes=[SinkLine],
    emits=[],
    binds=[("stream", FileOutputSink)],
)
def sink_file_sink(settings: dict[str, object]) -> SinkLineFileSink:
    # Alias for generic role naming: `sink` instead of `egress_file`.
    return egress_file_sink(settings)
