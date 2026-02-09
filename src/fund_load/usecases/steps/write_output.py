from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.adapters.file_io import FileOutputSink
from fund_load.usecases.messages import OutputLine
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/08 WriteOutput.md).
# consumes/emits are used for DAG construction (docs/framework/initial_stage/DAG construction.md).
@node(name="write_output", consumes=[OutputLine], emits=[])
@dataclass(frozen=True, slots=True)
class WriteOutput:
    # Step 08 writes formatted output via platform stream sink adapter.
    # Dependency injection: FileOutputSink is bound via adapter metadata + config binds.
    output_sink: FileOutputSink = inject.stream(FileOutputSink)

    def __call__(self, msg: OutputLine, ctx: object | None) -> list[OutputLine]:
        # Sink is responsible for persistence; step just delegates.
        self.output_sink.write_line(msg.json_text)
        return []
