from __future__ import annotations

from stream_kernel.execution.orchestration.runtime.startup_mode import (
    is_process_supervisor_mode,
    is_root_process_supervisor_runtime,
    is_worker_role,
    should_include_business_steps,
)


def test_startup_mode_root_process_supervisor_runtime_detected() -> None:
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha"}],
        }
    }
    assert is_process_supervisor_mode(runtime) is True
    assert is_worker_role(runtime) is False
    assert is_root_process_supervisor_runtime(runtime) is True
    assert should_include_business_steps(runtime) is False


def test_startup_mode_worker_process_supervisor_runtime_keeps_business_steps() -> None:
    runtime = {
        "__process_role": "worker",
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha"}],
        },
    }
    assert is_process_supervisor_mode(runtime) is True
    assert is_worker_role(runtime) is True
    assert is_root_process_supervisor_runtime(runtime) is False
    assert should_include_business_steps(runtime) is True


def test_startup_mode_local_runtime_keeps_business_steps() -> None:
    runtime = {
        "platform": {
            "bootstrap": {"mode": "local"},
        }
    }
    assert is_process_supervisor_mode(runtime) is False
    assert is_root_process_supervisor_runtime(runtime) is False
    assert should_include_business_steps(runtime) is True

