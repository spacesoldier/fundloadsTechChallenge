from __future__ import annotations

from dataclasses import dataclass

from fund_load.ports.output_sink import OutputSink
from fund_load.usecases.messages import OutputLine
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/08 WriteOutput.md).
@node(name="write_output")
@dataclass(frozen=True, slots=True)
class WriteOutput:
    # Step 08 writes formatted output via OutputSink (docs/implementation/steps/08 WriteOutput.md).
    # Dependency injection: OutputSink is provided by the runtime wiring.
    # We use the generic "stream" port_type for sinks in this initial stage.
    output_sink: OutputSink = inject.stream(OutputSink)

    def __call__(self, msg: OutputLine, ctx: object | None) -> list[OutputLine]:
        # Sink is responsible for persistence; step just delegates.
        self.output_sink.write_line(msg.json_text)
        return []
