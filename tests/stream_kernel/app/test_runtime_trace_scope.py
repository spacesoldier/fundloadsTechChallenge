from __future__ import annotations

import stream_kernel.app.runtime as runtime_module


def test_run_with_config_sets_and_restores_trace_scope_env(monkeypatch) -> None:
    config = {"runtime": {"platform": {}}}

    monkeypatch.setattr(runtime_module, "runtime_contract_summary", lambda _cfg: {})
    monkeypatch.setattr(runtime_module, "_configure_process_debug_env", lambda _cfg: None)
    monkeypatch.setattr(runtime_module.execution_builder, "build_runtime_artifacts", lambda *_a, **_k: object())
    monkeypatch.setattr(runtime_module.execution_builder, "execute_runtime_artifacts", lambda _artifacts: None)

    monkeypatch.setenv("STREAM_KERNEL_TRACE_SCOPE_ID", "scope-prev")

    exit_code = runtime_module.run_with_config(config, run_id="run")

    assert exit_code == 0
    assert runtime_module.os.environ.get("STREAM_KERNEL_TRACE_SCOPE_ID") == "scope-prev"
