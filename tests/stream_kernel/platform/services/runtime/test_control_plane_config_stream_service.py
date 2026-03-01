from __future__ import annotations

import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    DefaultControlPlaneConfigStreamService,
    InMemoryControlPlaneStartupConfigStore,
    YamlControlPlaneConfigStreamAdapter,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConfigStreamCompletedEvent,
    ExecutionGroupConfigRecord,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
)


def test_yaml_config_stream_adapter_emits_records_in_deterministic_section_order() -> None:
    adapter = YamlControlPlaneConfigStreamAdapter()
    runtime = {
        "strict": True,
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [
                {"name": "execution.features", "workers": 1, "nodes": ["n1"]},
                {"name": "execution.policy", "workers": 2, "nodes": ["n2"]},
            ],
        },
        "observability": {"enabled": True, "tracing": {"enabled": True}},
        "nodes": {
            "beta.node": {"retries": 2},
            "alpha.node": {"retries": 1},
        },
    }

    records = adapter.stream_records(runtime)

    assert [type(record) for record in records] == [
        SystemRuntimeConfigRecord,
        ObservabilityConfigRecord,
        ExecutionGroupConfigRecord,
        ExecutionGroupConfigRecord,
        NodeConfigRecord,
        NodeConfigRecord,
    ]
    # Node records must be deterministic even if runtime.nodes mapping was not inserted in sorted order.
    assert [record.record_id for record in records[-2:]] == [
        "node:alpha.node",
        "node:beta.node",
    ]


def test_yaml_config_stream_adapter_raises_deterministic_error_for_invalid_section_payload() -> None:
    adapter = YamlControlPlaneConfigStreamAdapter()
    runtime = {
        "platform": {
            "process_groups": "invalid",
        }
    }

    with pytest.raises(
        ValueError,
        match="control plane config stream: runtime.platform.process_groups must be a list",
    ):
        adapter.stream_records(runtime)


def test_config_stream_service_persists_typed_records_and_emits_completed_event() -> None:
    store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    adapter = YamlControlPlaneConfigStreamAdapter()
    service = DefaultControlPlaneConfigStreamService(adapter=adapter, store=store)
    runtime = {
        "strict": True,
        "platform": {"process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}]},
        "observability": {"enabled": True},
    }

    produced = service.stream(runtime)

    assert produced
    assert isinstance(produced[-1], ControlPlaneConfigStreamCompletedEvent)
    completed = produced[-1]
    assert completed.record_count == len(produced) - 1
    assert len(store.all_records()) == completed.record_count
    assert isinstance(store.records(section="system_runtime")[0], SystemRuntimeConfigRecord)


def test_config_store_returns_records_filtered_by_section() -> None:
    store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    adapter = YamlControlPlaneConfigStreamAdapter()
    service = DefaultControlPlaneConfigStreamService(adapter=adapter, store=store)
    runtime = {
        "strict": True,
        "platform": {
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
        },
        "observability": {"enabled": True},
        "nodes": {
            "node.a": {"foo": "bar"},
        },
    }

    _ = service.stream(runtime)

    assert [type(item) for item in store.records(section="execution_group")] == [ExecutionGroupConfigRecord]
    assert [type(item) for item in store.records(section="node")] == [NodeConfigRecord]
