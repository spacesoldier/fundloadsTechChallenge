from __future__ import annotations

from pathlib import Path

# WriteOutput behavior is specified in docs/implementation/steps/08 WriteOutput.md.
from fund_load.adapters.output_sink import FileOutputSink
from fund_load.usecases.messages import OutputLine
from fund_load.usecases.steps.write_output import WriteOutput


def test_write_output_writes_lines_in_order(tmp_path: Path) -> None:
    # WriteOutput should write OutputLine in input order.
    path = tmp_path / "out.txt"
    sink = FileOutputSink(path)
    step = WriteOutput(output_sink=sink)
    step(OutputLine(line_no=1, json_text='{"id":"1"}'), ctx=None)
    step(OutputLine(line_no=2, json_text='{"id":"2"}'), ctx=None)
    sink.close()
    assert path.read_text(encoding="utf-8") == '{"id":"1"}\n{"id":"2"}\n'
