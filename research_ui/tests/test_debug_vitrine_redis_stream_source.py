from __future__ import annotations

import json

from research_ui.debug_vitrine_backend import RedisDebugSource


def _source() -> RedisDebugSource:
    return RedisDebugSource(
        host="127.0.0.1",
        port=6379,
        db=0,
        password=None,
        key_prefix="stream_kernel:debug",
        connect_timeout_seconds=0.2,
        socket_timeout_seconds=1.0,
    )


def test_parse_stream_key_uses_xrange_and_extracts_stream_id() -> None:
    source = _source()
    redis_key = "stream_kernel:debug:runs:run-1:debug:root:supervisor:w1"
    payload = {
        "timestamp": "2026-03-09T00:00:00.000Z",
        "event": "runtime.inject.port_call",
        "source": "tests",
        "fields": {"payload_model": "DebugMessage"},
    }

    def _fake_command(parts: list[object]) -> object:
        if parts[:2] == ["TYPE", redis_key]:
            return "stream"
        if parts[:2] == ["XRANGE", redis_key]:
            return [["1741471225000-0", ["payload", json.dumps(payload)]]]
        raise AssertionError(f"unexpected command: {parts}")

    source._command = _fake_command  # type: ignore[method-assign]
    events, next_seq = source._parse_run_events_from_key(
        run_id="run-1",
        process_id="root:supervisor:w1",
        redis_key=redis_key,
        seq_start=7,
        max_events_per_process=100,
    )

    assert next_seq == 8
    assert len(events) == 1
    event = events[0]
    assert event["seq"] == 7
    assert event["event"] == "runtime.inject.port_call"
    assert event["process_id"] == "root:supervisor:w1"
    assert event["redis_stream_id"] == "1741471225000-0"


def test_parse_list_key_fallback_works_for_legacy_payloads() -> None:
    source = _source()
    redis_key = "stream_kernel:debug:runs:run-2:debug:leaf:execution.ingress:w1"
    payload = {
        "timestamp": "2026-03-09T00:00:01.000Z",
        "event": "legacy.event",
        "source": "tests",
        "fields": {},
    }

    def _fake_command(parts: list[object]) -> object:
        if parts[:2] == ["TYPE", redis_key]:
            return "list"
        if parts[:2] == ["LRANGE", redis_key]:
            return [json.dumps(payload)]
        raise AssertionError(f"unexpected command: {parts}")

    source._command = _fake_command  # type: ignore[method-assign]
    events, next_seq = source._parse_run_events_from_key(
        run_id="run-2",
        process_id="leaf:execution.ingress:w1",
        redis_key=redis_key,
        seq_start=0,
        max_events_per_process=10,
    )

    assert next_seq == 1
    assert len(events) == 1
    assert events[0]["event"] == "legacy.event"
    assert "redis_stream_id" not in events[0]
