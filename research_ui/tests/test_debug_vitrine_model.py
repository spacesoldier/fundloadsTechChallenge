from __future__ import annotations

from research_ui.debug_vitrine_model import (
    build_process_column_order,
    enrich_events_with_timeline,
    load_execution_group_order,
    parse_process_identity,
    sort_events_chronologically,
)


def test_parse_process_identity_leaf_and_root() -> None:
    leaf = parse_process_identity("leaf:execution.ingress:w3")
    root = parse_process_identity("root:supervisor:w1")

    assert leaf.role == "leaf"
    assert leaf.execution_group == "execution.ingress"
    assert leaf.worker_index == 3
    assert root.role == "root"
    assert root.execution_group == "supervisor"
    assert root.worker_index == 1


def test_build_process_column_order_prioritizes_observability_then_root_then_pipeline() -> None:
    ordered = build_process_column_order(
        [
            "leaf:execution.policy:w1",
            "root:supervisor:w1",
            "leaf:system.observability:w1",
            "leaf:execution.ingress:w1",
        ],
        execution_group_order=[
            "execution.ingress",
            "execution.features",
            "execution.policy",
            "execution.egress",
        ],
    )

    assert ordered == [
        "leaf:system.observability:w1",
        "root:supervisor:w1",
        "leaf:execution.ingress:w1",
        "leaf:execution.policy:w1",
    ]


def test_sort_events_chronologically_is_stable_for_same_timestamp() -> None:
    events = [
        {"timestamp": "2026-03-08T12:00:00.000Z", "process_id": "leaf:execution.policy:w1", "event": "b"},
        {"timestamp": "2026-03-08T12:00:00.000Z", "process_id": "leaf:execution.ingress:w1", "event": "a"},
        {"timestamp": "2026-03-08T12:00:01.000Z", "process_id": "root:supervisor:w1", "event": "c"},
    ]
    ordered = sort_events_chronologically(events)

    assert [item["event"] for item in ordered] == ["a", "b", "c"]


def test_enrich_events_with_timeline_sets_normalized_ratio() -> None:
    events = [
        {"timestamp": "2026-03-08T12:00:00.000Z", "process_id": "leaf:execution.ingress:w1", "event": "a"},
        {"timestamp": "2026-03-08T12:00:00.500Z", "process_id": "leaf:execution.features:w1", "event": "b"},
        {"timestamp": "2026-03-08T12:00:01.000Z", "process_id": "leaf:execution.egress:w1", "event": "c"},
    ]
    enriched = enrich_events_with_timeline(events)

    assert enriched[0]["timeline_ratio"] == 0.0
    assert enriched[1]["timeline_ratio"] == 0.5
    assert enriched[2]["timeline_ratio"] == 1.0


def test_load_execution_group_order_skips_observability_group() -> None:
    order = load_execution_group_order(
        {
            "runtime": {
                "platform": {
                    "process_groups": [
                        {"name": "execution.ingress"},
                        {"name": "system.observability"},
                        {"name": "execution.egress"},
                    ]
                }
            }
        }
    )

    assert order == ["execution.ingress", "execution.egress"]

