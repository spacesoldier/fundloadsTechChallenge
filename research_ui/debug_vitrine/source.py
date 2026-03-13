from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass

from research_ui.debug_vitrine_model import enrich_events_with_timeline

from .event_ops import (
    build_process_summary,
    extract_first_ts,
    extract_last_ts,
    normalize_redis_event_record,
)
from .helpers import as_int, as_optional_str, as_str, coerce_str_value, extract_stream_payload, pairs_to_dict
from .redis_wire import encode_command, read_reply
from .types import RunSnapshot

_LOG = logging.getLogger("research_ui.debug_vitrine")


@dataclass
class RedisDebugSource:
    host: str
    port: int
    db: int
    password: str | None
    key_prefix: str
    connect_timeout_seconds: float
    socket_timeout_seconds: float

    def load_run_snapshots(
        self,
        *,
        run_id: str | None = None,
        limit_runs: int = 20,
        max_events_per_process: int = 100_000,
    ) -> list[RunSnapshot]:
        run_ids = (
            [run_id]
            if isinstance(run_id, str) and run_id
            else self._run_ids(limit=limit_runs)
        )
        _LOG.info(
            "redis load_run_snapshots start run_id=%s limit_runs=%s discovered_run_ids=%s",
            run_id,
            limit_runs,
            len(run_ids),
        )
        snapshots: list[RunSnapshot] = []
        for rid in run_ids:
            snapshot = self._load_run_snapshot(
                rid,
                max_events_per_process=max_events_per_process,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        _LOG.info(
            "redis load_run_snapshots finished snapshots=%s",
            len(snapshots),
        )
        return snapshots

    def _run_ids(self, *, limit: int) -> list[str]:
        key = f"{self.key_prefix}:runs:index:by_time"
        raw = self._command(["ZREVRANGE", key, 0, max(0, int(limit) - 1)])
        if isinstance(raw, list):
            result = [
                item for item in (coerce_str_value(value) for value in raw) if item
            ]
            if result:
                _LOG.info(
                    "redis run ids loaded from zset key=%s count=%s",
                    key,
                    len(result),
                )
                return result
        report_key = f"{self.key_prefix}:runs:reports"
        reports = self._command(["HKEYS", report_key])
        if isinstance(reports, list):
            result = [
                item for item in (coerce_str_value(value) for value in reports) if item
            ][: max(1, int(limit))]
            if result:
                _LOG.info(
                    "redis run ids loaded from reports hash key=%s count=%s",
                    report_key,
                    len(result),
                )
                return result
        discovered = self._scan_run_ids(limit=limit)
        if discovered:
            _LOG.info("redis run ids loaded from scan count=%s", len(discovered))
            return discovered
        return []

    def _scan_run_ids(self, *, limit: int) -> list[str]:
        pattern = f"{self.key_prefix}:runs:meta:*"
        cursor = "0"
        found: list[str] = []
        while True:
            reply = self._command(["SCAN", cursor, "MATCH", pattern, "COUNT", 500])
            if not (isinstance(reply, list) and len(reply) == 2):
                break
            next_cursor = coerce_str_value(reply[0]) or "0"
            keys = reply[1] if isinstance(reply[1], list) else []
            for raw_key in keys:
                key = coerce_str_value(raw_key)
                if not isinstance(key, str):
                    continue
                run_id = key.removeprefix(f"{self.key_prefix}:runs:meta:")
                if run_id and run_id not in found:
                    found.append(run_id)
                    if len(found) >= max(1, int(limit)):
                        return found
            cursor = next_cursor
            if cursor == "0":
                break
        return found

    def _parse_run_events_from_key(
        self,
        *,
        run_id: str,
        process_id: str,
        redis_key: str,
        seq_start: int,
        max_events_per_process: int,
    ) -> tuple[list[dict[str, object]], int]:
        _LOG.info(
            "redis load key run_id=%s process_id=%s key=%s limit=%s",
            run_id,
            process_id,
            redis_key,
            max_events_per_process,
        )
        key_type = self._redis_key_type(redis_key)
        if key_type not in {"stream", "list"}:
            _LOG.info(
                "redis key missing/unsupported run_id=%s process_id=%s key=%s type=%s",
                run_id,
                process_id,
                redis_key,
                key_type,
            )
            return ([], seq_start)

        raw_items: object
        if key_type == "stream":
            raw_items = self._command(
                [
                    "XRANGE",
                    redis_key,
                    "-",
                    "+",
                    "COUNT",
                    max(0, int(max_events_per_process)),
                ]
            )
        else:
            raw_items = self._command(
                ["LRANGE", redis_key, 0, max(0, int(max_events_per_process) - 1)]
            )
        if not isinstance(raw_items, list):
            _LOG.info(
                "redis key invalid payload run_id=%s process_id=%s key=%s type=%s",
                run_id,
                process_id,
                redis_key,
                key_type,
            )
            return ([], seq_start)

        parsed_events: list[dict[str, object]] = []
        seq = seq_start
        for item in raw_items:
            payload: str | None
            stream_id: str | None = None
            if key_type == "stream":
                stream_id, payload = extract_stream_payload(item)
            else:
                payload = coerce_str_value(item)
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except Exception:
                continue
            event = normalize_redis_event_record(
                parsed=parsed,
                run_id=run_id,
                process_id=process_id,
            )
            if event is None:
                continue
            event.setdefault("run_id", run_id)
            event.setdefault("process_id", process_id)
            event["seq"] = seq
            if isinstance(stream_id, str) and stream_id:
                event["redis_stream_id"] = stream_id
            seq += 1
            parsed_events.append(event)
        _LOG.info(
            "redis key loaded run_id=%s process_id=%s key=%s type=%s records=%s",
            run_id,
            process_id,
            redis_key,
            key_type,
            len(parsed_events),
        )
        return (parsed_events, seq)

    def _events_for_process(
        self,
        *,
        run_id: str,
        process_id: str,
        max_events_per_process: int,
        seq_start: int,
    ) -> tuple[list[dict[str, object]], int]:
        keys = (
            f"{self.key_prefix}:runs:{run_id}:debug:{process_id}",
            f"{self.key_prefix}:runs:{run_id}:logs:{process_id}",
        )
        events: list[dict[str, object]] = []
        seq = seq_start
        for key in keys:
            chunk, seq = self._parse_run_events_from_key(
                run_id=run_id,
                process_id=process_id,
                redis_key=key,
                seq_start=seq,
                max_events_per_process=max_events_per_process,
            )
            events.extend(chunk)
        events.sort(
            key=lambda item: (as_str(item.get("timestamp")), as_str(item.get("event")))
        )
        return (events, seq)

    def _process_ids_from_scan(self, run_id: str) -> list[str]:
        pattern = f"{self.key_prefix}:runs:{run_id}:debug:*"
        pattern_logs = f"{self.key_prefix}:runs:{run_id}:logs:*"
        found: list[str] = []
        for active_pattern in (pattern, pattern_logs):
            cursor = "0"
            while True:
                reply = self._command(
                    ["SCAN", cursor, "MATCH", active_pattern, "COUNT", 500]
                )
                if not (isinstance(reply, list) and len(reply) == 2):
                    break
                next_cursor = coerce_str_value(reply[0]) or "0"
                keys = reply[1] if isinstance(reply[1], list) else []
                for raw_key in keys:
                    key = coerce_str_value(raw_key)
                    if not isinstance(key, str):
                        continue
                    process_id = key.rsplit(":", 1)[-1]
                    if process_id and process_id not in found:
                        found.append(process_id)
                cursor = next_cursor
                if cursor == "0":
                    break
        found.sort()
        _LOG.info(
            "redis process ids discovered from scan run_id=%s count=%s",
            run_id,
            len(found),
        )
        return found

    def _load_run_snapshot(
        self,
        run_id: str,
        *,
        max_events_per_process: int,
    ) -> RunSnapshot | None:
        meta_key = f"{self.key_prefix}:runs:meta:{run_id}"
        meta_raw = self._command(["HGETALL", meta_key])
        meta = pairs_to_dict(meta_raw)
        process_ids = self._process_ids(run_id)
        _LOG.info(
            "redis run snapshot start run_id=%s process_ids=%s",
            run_id,
            len(process_ids),
        )
        all_events: list[dict[str, object]] = []
        process_summaries = []
        seq = 0
        for process_id in process_ids:
            process_events, seq = self._events_for_process(
                run_id=run_id,
                process_id=process_id,
                max_events_per_process=max_events_per_process,
                seq_start=seq,
            )
            all_events.extend(process_events)
            first_ts = process_events[0].get("timestamp") if process_events else None
            last_ts = process_events[-1].get("timestamp") if process_events else None
            process_summaries.append(
                build_process_summary(
                    process_id=process_id,
                    first_ts=as_optional_str(first_ts),
                    last_ts=as_optional_str(last_ts),
                    event_count=len(process_events),
                )
            )
        if not all_events and not process_summaries:
            _LOG.info("redis run snapshot empty run_id=%s", run_id)
            return None
        sorted_events = enrich_events_with_timeline(all_events)
        total_records = as_int(meta.get("total_records"), default=len(sorted_events))
        logical_run_id = as_str(meta.get("logical_run_id"), default=run_id)
        first_ts = as_optional_str(meta.get("first_ts")) or extract_first_ts(sorted_events)
        last_ts = as_optional_str(meta.get("last_ts")) or extract_last_ts(sorted_events)
        _LOG.info(
            "redis run snapshot finished run_id=%s events=%s processes=%s",
            run_id,
            len(sorted_events),
            len(process_summaries),
        )
        return RunSnapshot(
            run_id=run_id,
            logical_run_id=logical_run_id,
            first_ts=first_ts,
            last_ts=last_ts,
            total_records=total_records,
            processes=tuple(process_summaries),
            events=tuple(sorted_events),
        )

    def _process_ids(self, run_id: str) -> list[str]:
        by_time = f"{self.key_prefix}:runs:{run_id}:processes:by_time"
        raw = self._command(["ZRANGE", by_time, 0, -1])
        if isinstance(raw, list):
            process_ids = [
                item for item in (coerce_str_value(value) for value in raw) if item
            ]
            if process_ids:
                _LOG.info(
                    "redis process ids loaded from zset run_id=%s count=%s",
                    run_id,
                    len(process_ids),
                )
                return process_ids
        plain = f"{self.key_prefix}:runs:{run_id}:processes"
        raw_set = self._command(["SMEMBERS", plain])
        if not isinstance(raw_set, list):
            _LOG.info("redis process ids key missing run_id=%s", run_id)
            return self._process_ids_from_scan(run_id)
        process_ids = [
            item for item in (coerce_str_value(value) for value in raw_set) if item
        ]
        if process_ids:
            process_ids.sort()
            _LOG.info(
                "redis process ids loaded from set run_id=%s count=%s",
                run_id,
                len(process_ids),
            )
            return process_ids
        discovered = self._process_ids_from_scan(run_id)
        if discovered:
            return discovered
        return []

    def _redis_key_type(self, redis_key: str) -> str:
        raw = self._command(["TYPE", redis_key])
        resolved = coerce_str_value(raw)
        if not isinstance(resolved, str):
            return ""
        return resolved.strip().lower()

    def _command(self, parts: list[object]) -> object:
        command_name = as_str(parts[0]) if parts else "unknown"
        _LOG.debug(
            "redis command host=%s port=%s db=%s cmd=%s",
            self.host,
            self.port,
            self.db,
            command_name,
        )
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.connect_timeout_seconds,
            ) as conn:
                conn.settimeout(self.socket_timeout_seconds)
                prelude: list[list[object]] = []
                if isinstance(self.password, str) and self.password:
                    prelude.append(["AUTH", self.password])
                prelude.append(["SELECT", self.db])
                wire = b"".join(encode_command(command) for command in prelude + [parts])
                conn.sendall(wire)
                for _ in prelude:
                    _ = read_reply(conn)
                return read_reply(conn)
        except Exception:
            _LOG.exception(
                "redis command failed host=%s port=%s db=%s cmd=%s",
                self.host,
                self.port,
                self.db,
                command_name,
            )
            raise
