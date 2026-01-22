from __future__ import annotations

from dataclasses import dataclass

from fund_load.ports.output_sink import OutputSink
from fund_load.usecases.messages import OutputLine


@dataclass(frozen=True, slots=True)
class WriteOutput:
    # Step 08 writes formatted output via OutputSink (docs/implementation/steps/08 WriteOutput.md).
    output_sink: OutputSink

    def __call__(self, msg: OutputLine, ctx: object | None) -> list[OutputLine]:
        # Sink is responsible for persistence; step just delegates.
        self.output_sink.write_line(msg.json_text)
        return []
