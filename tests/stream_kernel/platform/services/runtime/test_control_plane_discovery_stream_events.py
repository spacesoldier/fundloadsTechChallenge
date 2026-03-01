from __future__ import annotations

import pytest

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    discovery_entity_sort_key,
)


def test_discovery_entity_record_validates_kind_scope_and_metadata() -> None:
    record = ControlPlaneDiscoveryEntityRecord(
        entity_kind="node",
        entity_id="pkg.mod:NodeA",
        source_scope="platform",
        module="pkg.mod",
        qualname="NodeA",
        meta={"consumes": ["A"], "emits": ["B"]},
    )
    assert record.entity_kind == "node"
    assert record.source_scope == "platform"

    with pytest.raises(ValueError, match="entity_kind"):
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="unknown",
            entity_id="pkg.mod:X",
            source_scope="platform",
            module="pkg.mod",
            qualname="X",
            meta={},
        )
    with pytest.raises(ValueError, match="source_scope"):
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="service",
            entity_id="pkg.mod:S",
            source_scope="other",
            module="pkg.mod",
            qualname="S",
            meta={},
        )
    with pytest.raises(ValueError, match="meta"):
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="adapter",
            entity_id="pkg.mod:A",
            source_scope="project",
            module="pkg.mod",
            qualname="A",
            meta=[],  # type: ignore[arg-type]
        )


def test_discovery_start_requested_validates_runtime_mapping() -> None:
    event = ControlPlaneDiscoveryStartRequestedEvent(runtime={"platform": {"process_groups": []}})
    assert isinstance(event.runtime, dict)

    with pytest.raises(ValueError, match="runtime"):
        ControlPlaneDiscoveryStartRequestedEvent(runtime=[])  # type: ignore[arg-type]


def test_discovery_batch_requested_validates_cursor_and_limit() -> None:
    event = ControlPlaneDiscoveryBatchRequestedEvent(
        session_id="s1",
        source_scope="project",
        cursor=0,
        limit=128,
    )
    assert event.limit == 128

    with pytest.raises(ValueError, match="cursor"):
        ControlPlaneDiscoveryBatchRequestedEvent(
            session_id="s1",
            source_scope="project",
            cursor=-1,
            limit=128,
        )
    with pytest.raises(ValueError, match="limit"):
        ControlPlaneDiscoveryBatchRequestedEvent(
            session_id="s1",
            source_scope="project",
            cursor=0,
            limit=0,
        )


def test_discovery_batch_ready_validates_next_cursor_contract() -> None:
    record = ControlPlaneDiscoveryEntityRecord(
        entity_kind="node",
        entity_id="pkg.mod:NodeA",
        source_scope="platform",
        module="pkg.mod",
        qualname="NodeA",
        meta={},
    )

    ok = ControlPlaneDiscoveryBatchReadyEvent(
        session_id="s1",
        source_scope="platform",
        cursor=0,
        entities=(record,),
        has_more=True,
        next_cursor=1,
    )
    assert ok.has_more is True
    assert ok.next_cursor == 1

    with pytest.raises(ValueError, match="next_cursor"):
        ControlPlaneDiscoveryBatchReadyEvent(
            session_id="s1",
            source_scope="platform",
            cursor=0,
            entities=(record,),
            has_more=True,
            next_cursor=None,
        )
    with pytest.raises(ValueError, match="next_cursor"):
        ControlPlaneDiscoveryBatchReadyEvent(
            session_id="s1",
            source_scope="platform",
            cursor=2,
            entities=(record,),
            has_more=True,
            next_cursor=2,
        )
    with pytest.raises(ValueError, match="next_cursor"):
        ControlPlaneDiscoveryBatchReadyEvent(
            session_id="s1",
            source_scope="platform",
            cursor=0,
            entities=(record,),
            has_more=False,
            next_cursor=1,
        )


def test_discovery_source_completed_validates_totals() -> None:
    completed = ControlPlaneDiscoverySourceCompletedEvent(
        session_id="s1",
        source_scope="project",
        total_emitted=5,
    )
    assert completed.total_emitted == 5

    with pytest.raises(ValueError, match="total_emitted"):
        ControlPlaneDiscoverySourceCompletedEvent(
            session_id="s1",
            source_scope="project",
            total_emitted=-1,
        )


def test_discovery_entity_sort_key_orders_platform_before_project_then_module_qualname() -> None:
    records = [
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="service",
            entity_id="b.mod:S2",
            source_scope="project",
            module="b.mod",
            qualname="S2",
            meta={},
        ),
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="node",
            entity_id="a.mod:N1",
            source_scope="platform",
            module="a.mod",
            qualname="N1",
            meta={},
        ),
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="adapter",
            entity_id="a.mod:A1",
            source_scope="platform",
            module="a.mod",
            qualname="A1",
            meta={},
        ),
    ]

    ordered = sorted(records, key=discovery_entity_sort_key)
    assert [item.entity_id for item in ordered] == [
        "a.mod:A1",
        "a.mod:N1",
        "b.mod:S2",
    ]
