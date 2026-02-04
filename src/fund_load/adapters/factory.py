from __future__ import annotations

from pathlib import Path
from typing import Any

from fund_load.adapters.input_source import FileInputSource
from fund_load.adapters.output_sink import FileOutputSink
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore


def file_input_source(settings: dict[str, Any]) -> FileInputSource:
    # Factory for file-based input source.
    return FileInputSource(Path(settings["path"]))


def file_output_sink(settings: dict[str, Any]) -> FileOutputSink:
    # Factory for file-based output sink.
    return FileOutputSink(Path(settings["path"]))


def window_store_memory(settings: dict[str, Any]) -> InMemoryWindowStore:
    # Factory for in-memory window store.
    return InMemoryWindowStore()


def prime_checker_stub(settings: dict[str, Any]) -> SievePrimeChecker:
    # Factory for stubbed prime checker (sieve).
    return SievePrimeChecker.from_max(int(settings.get("max_id", 0)))
