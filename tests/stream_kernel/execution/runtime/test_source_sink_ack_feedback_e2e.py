from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService


@dataclass(frozen=True, slots=True)
class ReadNextRecordCommand:
    reason: str
    after_seq: int | None = None


@dataclass(frozen=True, slots=True)
class SourceRecord:
    seq: int
    raw_value: str
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class ProcessedRecord:
    seq: int
    processed_value: str
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class SinkAckEvent:
    seq: int
    tombstone: bool
    payload_class: str


@dataclass(slots=True)
class _SourceNode:
    records: tuple[str, ...]
    emitted: list[SourceRecord] = field(default_factory=list)
    completed: bool = False
    _cursor: int = 0

    def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
        if not isinstance(payload, ReadNextRecordCommand):
            return []
        # Tombstone handling is finalized at source: any late/extra read commands are ignored.
        if self.completed:
            return []
        if self._cursor >= len(self.records):
            self.completed = True
            return []
        seq = self._cursor + 1
        tombstone = seq == len(self.records)
        record = SourceRecord(
            seq=seq,
            raw_value=self.records[self._cursor],
            tombstone=tombstone,
        )
        self._cursor += 1
        if tombstone:
            self.completed = True
        self.emitted.append(record)
        return [record]


@dataclass(slots=True)
class _TransformNode:
    seen: list[SourceRecord] = field(default_factory=list)

    def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
        if not isinstance(payload, SourceRecord):
            return []
        self.seen.append(payload)
        return [
            ProcessedRecord(
                seq=payload.seq,
                processed_value=f"processed:{payload.raw_value}",
                tombstone=payload.tombstone,
            )
        ]


@dataclass(slots=True)
class _SinkNode:
    seen: list[ProcessedRecord] = field(default_factory=list)

    def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
        if not isinstance(payload, ProcessedRecord):
            return []
        self.seen.append(payload)
        return [
            SinkAckEvent(
                seq=payload.seq,
                tombstone=payload.tombstone,
                payload_class=type(payload).__name__,
            )
        ]


@dataclass(slots=True)
class _AckToReadNextNode:
    seen: list[SinkAckEvent] = field(default_factory=list)

    def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
        if not isinstance(payload, SinkAckEvent):
            return []
        self.seen.append(payload)
        return [ReadNextRecordCommand(reason="sink_ack", after_seq=payload.seq)]


def _build_runner(
    *,
    profile: str,
    nodes: dict[str, object],
    queue: InMemoryQueue,
    routing: RoutingService,
) -> SyncRunner | AsyncRunner:
    kwargs = dict(
        nodes=nodes,
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=routing,
        observability=NoOpObservabilityService(),
    )
    if profile == "sync":
        return SyncRunner(**kwargs)
    return AsyncRunner(**kwargs)


@pytest.mark.parametrize("runner_profile", ["sync", "async"])
def test_e2e_source_sink_ack_feedback_reads_exactly_100_records(runner_profile: str) -> None:
    records = tuple(f"record-{idx:03d}" for idx in range(1, 101))
    source = _SourceNode(records=records)
    transform = _TransformNode()
    sink = _SinkNode()
    ack_to_next = _AckToReadNextNode()

    queue = InMemoryQueue()
    queue.push(
        Envelope(
            payload=ReadNextRecordCommand(reason="start"),
            target="source:test_records",
            trace_id="trace-e2e",
        )
    )
    routing = RoutingService(
        registry=InMemoryConsumerRegistry(
            {
                ReadNextRecordCommand: ["source:test_records"],
                SourceRecord: ["transform:test_records"],
                ProcessedRecord: ["sink:test_records"],
                SinkAckEvent: ["node:sink_ack_to_read_next"],
            }
        ),
        strict=True,
    )
    runner = _build_runner(
        profile=runner_profile,
        nodes={
            "source:test_records": source,
            "transform:test_records": transform,
            "sink:test_records": sink,
            "node:sink_ack_to_read_next": ack_to_next,
        },
        queue=queue,
        routing=routing,
    )

    runner.run()

    assert len(source.emitted) == 100
    assert len(transform.seen) == 100
    assert len(sink.seen) == 100
    assert len(ack_to_next.seen) == 100
    assert source.emitted[-1].tombstone is True
    assert sink.seen[-1].tombstone is True
    assert ack_to_next.seen[-1].tombstone is True
    assert all(item.payload_class == "ProcessedRecord" for item in ack_to_next.seen)
    assert [item.seq for item in sink.seen] == list(range(1, 101))
    assert source.completed is True
    assert queue.size() == 0
