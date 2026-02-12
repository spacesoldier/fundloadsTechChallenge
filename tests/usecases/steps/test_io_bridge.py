from __future__ import annotations

from fund_load.domain.messages import RawLine
from fund_load.usecases.messages import OutputLine
from fund_load.usecases.steps.io_bridge import EgressLineBridge, IngressLineBridge
from stream_kernel.adapters.file_io import ByteRecord, SinkLine, TextRecord
import pytest


def test_ingress_line_bridge_maps_text_record_to_raw_line() -> None:
    bridge = IngressLineBridge()
    out = bridge(TextRecord(text='{"id":"8"}', seq=8, source="ingress_file", encoding="utf-8"), ctx=None)
    assert out == [RawLine(line_no=8, raw_text='{"id":"8"}')]


def test_ingress_line_bridge_requires_seq_on_text_record() -> None:
    bridge = IngressLineBridge()
    with pytest.raises(ValueError):
        bridge(TextRecord(text='{"id":"9"}', seq=None, source="ingress_file", encoding="utf-8"), ctx=None)


def test_egress_line_bridge_maps_output_line_to_sink_line() -> None:
    bridge = EgressLineBridge()
    out = bridge(OutputLine(line_no=3, json_text='{"ok":true}'), ctx=None)
    assert out == [SinkLine(text='{"ok":true}', seq=3)]


def test_ingress_line_bridge_rejects_byte_record() -> None:
    # Decode policy belongs to platform adapter; bridge accepts only text transport model.
    bridge = IngressLineBridge()
    with pytest.raises(TypeError):
        bridge(ByteRecord(payload=b'{"id":"7"}', seq=7, source="ingress_file"), ctx=None)
