from __future__ import annotations

import os

_TRACE_SCOPE_ENV = "STREAM_KERNEL_TRACE_SCOPE_ID"


def scoped_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id:
        return "run"
    scope = os.getenv(_TRACE_SCOPE_ENV)
    if not isinstance(scope, str) or not scope:
        return run_id
    return f"{run_id}@{scope}"


def scoped_trace_id_for_index(*, run_id: str, index: int) -> str:
    return f"{scoped_run_id(run_id)}:{int(index)}"


def scoped_trace_id_for_source(*, run_id: str, role: str, sequence: int) -> str:
    role_text = role if isinstance(role, str) and role else "source"
    return f"{scoped_run_id(run_id)}:{role_text}:{int(sequence)}"


__all__ = [
    "scoped_run_id",
    "scoped_trace_id_for_index",
    "scoped_trace_id_for_source",
]
