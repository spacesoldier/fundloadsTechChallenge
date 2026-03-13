from __future__ import annotations

from research_ui.debug_vitrine_backend import (
    DebugVitrineService,
    InMemoryRunStore,
    ProcessSummary,
    RunSnapshot,
)


class _FakeSource:
    def __init__(self, snapshots: list[RunSnapshot]) -> None:
        self._snapshots = snapshots

    def load_run_snapshots(
        self,
        *,
        run_id: str | None = None,
        limit_runs: int = 20,
        max_events_per_process: int = 100000,
    ) -> list[RunSnapshot]:
        _ = (run_id, limit_runs, max_events_per_process)
        return list(self._snapshots)


def _snapshot() -> RunSnapshot:
    return RunSnapshot(
        run_id="run:demo",
        logical_run_id="run",
        first_ts="2026-03-08T12:00:00.000Z",
        last_ts="2026-03-08T12:00:01.000Z",
        total_records=3,
        processes=(
            ProcessSummary(
                process_id="leaf:system.observability:w1",
                process_role="leaf",
                execution_group="system.observability",
                worker_index=1,
                event_count=1,
                first_ts="2026-03-08T12:00:00.100Z",
                last_ts="2026-03-08T12:00:00.100Z",
            ),
            ProcessSummary(
                process_id="root:supervisor:w1",
                process_role="root",
                execution_group="supervisor",
                worker_index=1,
                event_count=1,
                first_ts="2026-03-08T12:00:00.500Z",
                last_ts="2026-03-08T12:00:00.500Z",
            ),
            ProcessSummary(
                process_id="leaf:execution.ingress:w1",
                process_role="leaf",
                execution_group="execution.ingress",
                worker_index=1,
                event_count=1,
                first_ts="2026-03-08T12:00:00.900Z",
                last_ts="2026-03-08T12:00:00.900Z",
            ),
        ),
        events=(
            {
                "timestamp": "2026-03-08T12:00:00.100Z",
                "process_id": "leaf:system.observability:w1",
                "event": "system.obs.trace_dispatch",
                "source": "stream_kernel.execution.transport.ipc",
                "fields": {},
                "seq": 0,
            },
            {
                "timestamp": "2026-03-08T12:00:00.500Z",
                "process_id": "root:supervisor:w1",
                "event": "runtime.inject.port_call",
                "source": "stream_kernel.execution.transport.ipc",
                "fields": {
                    "port_type": "ipc",
                    "method": "send",
                    "payload_model": "fund_load.domain.messages.LoadAttemptParsed",
                    "payload": {"attempt_id": "A-1"},
                },
                "seq": 1,
            },
            {
                "timestamp": "2026-03-08T12:00:00.900Z",
                "process_id": "leaf:execution.ingress:w1",
                "event": "leaf.command_loop.config_card_processed",
                "source": "stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service",
                "fields": {"payload_model": "fund_load.domain.messages.LoadAttemptParsed"},
                "seq": 2,
            },
        ),
    )


def test_reload_from_redis_populates_memory_store() -> None:
    service = DebugVitrineService(
        redis_source=_FakeSource([_snapshot()]),  # type: ignore[arg-type]
        memory_store=InMemoryRunStore(),
        stores=[],
    )

    result = service.reload_from_redis()
    runs = service.list_runs(limit=10)

    assert result["loaded_runs"] == 1
    assert result["loaded_events"] == 3
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run:demo"


def test_run_events_returns_column_order_and_marks_communication_events() -> None:
    service = DebugVitrineService(
        redis_source=_FakeSource([_snapshot()]),  # type: ignore[arg-type]
        memory_store=InMemoryRunStore(),
        stores=[],
    )
    _ = service.reload_from_redis()
    payload = service.run_events(
        run_id="run:demo",
        execution_group_order=["execution.ingress", "execution.features", "execution.policy", "execution.egress"],
    )

    assert payload["column_order"] == [
        "leaf:system.observability:w1",
        "root:supervisor:w1",
        "leaf:execution.ingress:w1",
    ]
    events = payload["events"]
    assert len(events) == 3
    assert events[1]["is_communication"] is True
    assert events[2]["is_communication"] is False


def test_query_events_filters_by_model_and_time_window() -> None:
    service = DebugVitrineService(
        redis_source=_FakeSource([_snapshot()]),  # type: ignore[arg-type]
        memory_store=InMemoryRunStore(),
        stores=[],
    )
    _ = service.reload_from_redis()

    payload = service.query_events(
        run_id="run:demo",
        execution_group_order=["execution.ingress"],
        payload_model="fund_load.domain.messages.LoadAttemptParsed",
        timestamp_from="2026-03-08T12:00:00.400Z",
        timestamp_to="2026-03-08T12:00:00.950Z",
        limit=10,
        offset=0,
    )

    assert payload["total_matched"] == 2
    events = payload["events"]
    assert len(events) == 2
    assert events[0]["event"] == "runtime.inject.port_call"
    assert events[1]["event"] == "leaf.command_loop.config_card_processed"
