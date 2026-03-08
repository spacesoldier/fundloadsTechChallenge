from __future__ import annotations

from datetime import UTC, datetime

from stream_kernel.observability.adapters.debug import RedisDebugSink, debug_redis
from stream_kernel.observability.domain.debug import DebugMessage


def _sink() -> RedisDebugSink:
    return RedisDebugSink(
        host="127.0.0.1",
        port=6379,
        db=0,
        password=None,
        key_prefix="stream_kernel:debug",
        ttl_seconds=3600,
        connect_timeout_seconds=0.2,
        socket_timeout_seconds=1.0,
        write_mode="inline",
        queue_max_items=128,
        batch_max_items=32,
        batch_flush_interval_ms=10,
    )


def _message(*, process_group: str, worker_id: str) -> DebugMessage:
    return DebugMessage(
        timestamp=datetime.now(tz=UTC),
        event="runtime.inject.port_call",
        source="tests",
        fields={},
        run_id="run-42",
        run_instance_id="run-instance-42",
        process_group=process_group,
        worker_id=worker_id,
        trace_id=None,
    )


def test_debug_redis_uses_root_group_and_worker_index_in_key() -> None:
    sink = _sink()
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]

    sink.emit(_message(process_group="supervisor", worker_id="supervisor#1"))

    assert len(captured) == 1
    commands = captured[0]
    assert any(
        cmd[:2] == ["RPUSH", "stream_kernel:debug:runs:run-42:debug:root:supervisor:w1"]
        for cmd in commands
    )
    assert any(
        cmd == ["SADD", "stream_kernel:debug:runs:run-42:processes", "root:supervisor:w1"]
        for cmd in commands
    )


def test_debug_redis_uses_leaf_group_and_worker_index_in_key() -> None:
    sink = _sink()
    captured: list[list[list[object]]] = []
    sink._execute = lambda commands: captured.append(commands)  # type: ignore[method-assign]

    sink.emit(_message(process_group="execution.features", worker_id="execution.features#3"))

    assert len(captured) == 1
    commands = captured[0]
    assert any(
        cmd[:2] == ["RPUSH", "stream_kernel:debug:runs:run-42:debug:leaf:execution.features:w3"]
        for cmd in commands
    )
    assert any(
        cmd[:3]
        == [
            "HSET",
            "stream_kernel:debug:debug:index:process",
            "run-42:leaf:execution.features:w3",
        ]
        for cmd in commands
    )


def test_debug_redis_adapter_accepts_background_settings() -> None:
    sink = debug_redis(
        {
            "host": "127.0.0.1",
            "port": 6379,
            "db": 0,
            "write_mode": "background",
            "queue_max_items": 256,
            "batch_max_items": 16,
            "batch_flush_interval_ms": 5,
        }
    )
    assert isinstance(sink, RedisDebugSink)
    sink.close()
