from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
_ = fastapi
from fastapi.testclient import TestClient

from research_ui import webapp


class _FakeVitrineService:
    def query_events(self, **kwargs: object) -> dict[str, object]:
        return {
            "run_id": kwargs["run_id"],
            "column_order": ["root:supervisor:w1"],
            "processes": [{"process_id": "root:supervisor:w1"}],
            "events": [],
            "event_count": 0,
            "total_matched": 0,
            "limit": kwargs.get("limit", 0),
            "offset": kwargs.get("offset", 0),
            "filters": {
                "process_id": kwargs.get("process_id"),
                "event_name": kwargs.get("event_name"),
                "payload_model": kwargs.get("payload_model"),
                "timestamp_from": kwargs.get("timestamp_from"),
                "timestamp_to": kwargs.get("timestamp_to"),
            },
        }


def test_api_debug_run_events_query(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "_vitrine_service", _FakeVitrineService())
    client = TestClient(webapp.app)

    response = client.post(
        "/api/debug/runs/run:demo/events/query",
        json={
            "process_id": "leaf:execution.ingress:w1",
            "payload_model": "fund_load.domain.messages.LoadAttemptParsed",
            "limit": 123,
            "offset": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run:demo"
    assert body["limit"] == 123
    assert body["offset"] == 5
    assert body["filters"]["process_id"] == "leaf:execution.ingress:w1"
    assert body["filters"]["payload_model"] == "fund_load.domain.messages.LoadAttemptParsed"
