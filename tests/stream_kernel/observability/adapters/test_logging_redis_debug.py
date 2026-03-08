from __future__ import annotations

from stream_kernel.observability.adapters.logging import RedisDebugLogSink, log_redis_debug
from stream_kernel.observability.domain.logging import LogMessage


def _sink(*, only_debug_channel: bool) -> RedisDebugLogSink:
    return RedisDebugLogSink(
        host="127.0.0.1",
        port=6379,
        db=0,
        password=None,
        key_prefix="stream_kernel:debug",
        ttl_seconds=3600,
        connect_timeout_seconds=0.2,
        socket_timeout_seconds=1.0,
        only_debug_channel=only_debug_channel,
    )


def test_redis_debug_log_sink_skips_non_debug_messages_when_only_debug_channel_enabled() -> None:
    sink = _sink(only_debug_channel=True)
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]
    sink.emit(
        LogMessage(
            level="info",
            message="regular message",
            fields={"process_name": "supervisor", "run_id": "run-1"},
        )
    )
    assert captured == []


def test_redis_debug_log_sink_writes_run_and_process_indexes() -> None:
    sink = _sink(only_debug_channel=False)
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]
    sink.emit(
        LogMessage(
            level="debug",
            message="runtime debug",
            fields={
                "debug_channel": "runtime_debug",
                "run_id": "run-123",
                "process_name": "supervisor",
            },
        )
    )
    assert len(captured) == 1
    commands = captured[0]
    assert any(cmd[:2] == ["RPUSH", "stream_kernel:debug:runs:run-123:logs:root:supervisor:w1"] for cmd in commands)
    assert any(cmd == ["SADD", "stream_kernel:debug:runs:run-123:processes", "root:supervisor:w1"] for cmd in commands)
    assert any(cmd[:2] == ["ZADD", "stream_kernel:debug:runs:index:by_time"] and cmd[3] == "run-123" for cmd in commands)
    assert any(cmd[:3] == ["HSET", "stream_kernel:debug:runs:reports", "run-123"] for cmd in commands)
    assert any(cmd[:3] == ["HSET", "stream_kernel:debug:logs:index:process", "run-123:root:supervisor:w1"] for cmd in commands)


def test_redis_debug_log_sink_namespaces_default_run_and_stores_summary_fields() -> None:
    sink = _sink(only_debug_channel=False)
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]
    sink.emit(
        LogMessage(
            level="debug",
            message="summary",
            fields={
                "debug_channel": "runtime_debug",
                "process_name": "supervisor",
                "debug_summary": True,
                "status": "completed",
            },
        )
    )
    assert len(captured) == 1
    commands = captured[0]
    report_commands = [cmd for cmd in commands if len(cmd) >= 4 and cmd[:2] == ["HSET", "stream_kernel:debug:runs:reports"]]
    assert report_commands
    run_id = report_commands[0][2]
    assert isinstance(run_id, str) and run_id.startswith("run:")
    assert any(
        cmd[:3] == ["HSET", f"stream_kernel:debug:runs:meta:{run_id}", "summary:status"] and cmd[3] == "completed"
        for cmd in commands
    )


def test_redis_debug_log_sink_uses_shared_env_run_instance_for_default_run(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_RUN_INSTANCE_ID", "launch-001")
    sink = _sink(only_debug_channel=False)
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]
    sink.emit(
        LogMessage(
            level="info",
            message="lifecycle event",
            fields={"process_name": "supervisor"},
        )
    )
    assert len(captured) == 1
    commands = captured[0]
    assert any(cmd[:2] == ["RPUSH", "stream_kernel:debug:runs:run:launch-001:logs:root:supervisor:w1"] for cmd in commands)
    assert any(
        cmd[:3] == ["HSET", "stream_kernel:debug:runs:reports", "run:launch-001"]
        for cmd in commands
    )


def test_log_redis_debug_capture_all_events_overrides_debug_filter() -> None:
    sink = log_redis_debug({"capture_all_events": True})
    assert sink._only_debug_channel is False  # type: ignore[attr-defined]
