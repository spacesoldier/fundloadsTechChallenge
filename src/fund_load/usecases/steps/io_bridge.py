from __future__ import annotations

from dataclasses import dataclass

from fund_load.domain.messages import RawLine
from fund_load.usecases.messages import OutputLine
from stream_kernel.adapters.file_io import ByteRecord, SinkLine, TextRecord
from stream_kernel.kernel.node import node


@node(name="ingress_line_bridge", consumes=[TextRecord], emits=[RawLine])
@dataclass(frozen=True, slots=True)
class IngressLineBridge:
    # Converts framework text transport payload into project domain RawLine.
    def __call__(self, msg: TextRecord | ByteRecord, ctx: object | None) -> list[RawLine]:
        _ = ctx
        if not isinstance(msg, TextRecord):
            raise TypeError("IngressLineBridge accepts only TextRecord payloads")
        if msg.seq is None:
            raise ValueError("IngressLineBridge requires TextRecord.seq for deterministic ordering")
        return [RawLine(line_no=msg.seq, raw_text=msg.text)]


@node(name="egress_line_bridge", consumes=[OutputLine], emits=[SinkLine])
@dataclass(frozen=True, slots=True)
class EgressLineBridge:
    # Converts project output payload into framework sink payload.
    def __call__(self, msg: OutputLine, ctx: object | None) -> list[SinkLine]:
        return [SinkLine(text=msg.json_text, seq=msg.line_no)]
