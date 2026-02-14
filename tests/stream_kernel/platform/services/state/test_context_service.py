from __future__ import annotations

from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.integration.kv_store import InMemoryKvStore


def test_context_service_seeds_and_reads_metadata_view() -> None:
    # Service owns context lifecycle and exposes metadata view for regular nodes.
    store = InMemoryKvStore()
    service = InMemoryKvContextService(store)

    service.seed(trace_id="t1", payload={"id": 1}, run_id="run", scenario_id="scenario")
    store.set("t1", {"__trace_id": "t1", "__run_id": "run", "line_no": 3, "k": "v"})

    assert service.metadata("t1", full=False) == {"line_no": 3, "k": "v"}


def test_context_service_returns_full_context_for_service_nodes() -> None:
    # Service nodes can request complete context including reserved framework keys.
    store = InMemoryKvStore()
    service = InMemoryKvContextService(store)
    store.set("t1", {"__trace_id": "t1", "__run_id": "run", "k": "v"})

    assert service.metadata("t1", full=True) == {"__trace_id": "t1", "__run_id": "run", "k": "v"}


def test_context_service_returns_empty_metadata_when_missing() -> None:
    # Missing trace context should not fail execution.
    service = InMemoryKvContextService(InMemoryKvStore())
    assert service.metadata("missing", full=False) == {}


def test_context_service_wraps_non_mapping_values() -> None:
    # Non-dict values are normalized to preserve deterministic runner behavior.
    store = InMemoryKvStore()
    service = InMemoryKvContextService(store)
    store.set("t1", 123)

    assert service.metadata("t1", full=True) == {"value": 123}


def test_context_service_seeds_transport_seq_when_payload_has_seq() -> None:
    # Transport sequence is persisted in internal context for ordered sink modes.
    class _Payload:
        seq = 7

    store = InMemoryKvStore()
    service = InMemoryKvContextService(store)
    service.seed(trace_id="t1", payload=_Payload(), run_id="run", scenario_id="scenario")

    assert service.metadata("t1", full=True).get("__seq") == 7


def test_context_service_seeds_reply_to_when_provided() -> None:
    # Reply correlation metadata should be persisted in internal context.
    store = InMemoryKvStore()
    service = InMemoryKvContextService(store)
    service.seed(
        trace_id="t1",
        payload={"id": 1},
        run_id="run",
        scenario_id="scenario",
        reply_to="http:req-1",
    )

    assert service.metadata("t1", full=True).get("__reply_to") == "http:req-1"
