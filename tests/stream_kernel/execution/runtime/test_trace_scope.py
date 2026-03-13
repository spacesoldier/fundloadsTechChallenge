from __future__ import annotations

from stream_kernel.execution.runtime.trace_scope import (
    scoped_trace_id_for_index,
    scoped_trace_id_for_source,
)


def test_scoped_trace_id_for_index_without_scope_env(monkeypatch) -> None:
    monkeypatch.delenv("STREAM_KERNEL_TRACE_SCOPE_ID", raising=False)

    assert scoped_trace_id_for_index(run_id="run", index=7) == "run:7"


def test_scoped_trace_id_for_index_with_scope_env(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_TRACE_SCOPE_ID", "launch-001")

    assert scoped_trace_id_for_index(run_id="run", index=7) == "run@launch-001:7"


def test_scoped_trace_id_for_source_with_scope_env(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_TRACE_SCOPE_ID", "launch-002")

    assert (
        scoped_trace_id_for_source(run_id="run", role="events_source", sequence=3)
        == "run@launch-002:events_source:3"
    )
