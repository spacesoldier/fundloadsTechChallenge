from __future__ import annotations

import multiprocessing as mp
import traceback
from pathlib import Path

import pytest

from stream_kernel.app.runtime import run_with_config
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config


def _run_runtime_config_subprocess(config: dict[str, object], result_queue: object) -> None:
    put = getattr(result_queue, "put", None)
    if not callable(put):
        return
    try:
        rc = run_with_config(config, run_id="fund_load_e2e")
        put({"status": "ok", "rc": int(rc)})
    except Exception as exc:  # noqa: BLE001 - return deterministic test diagnostic payload.
        put(
            {
                "status": "error",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )


def _tail(path: Path, *, max_lines: int = 40) -> str:
    if not path.exists():
        return "<missing>"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max(1, int(max_lines)) :])


def _input_line(idx: int) -> str:
    return (
        f'{{"id":"{idx}","customer_id":"10","load_amount":"$10.00",'
        f'"time":"2025-01-01T00:00:{idx:02d}Z"}}'
    )


@pytest.mark.xfail(
    reason=(
        "Known issue: root/leaf multiprocess baseline-like pipeline stalls after "
        "start-work enqueue (no downstream progress in data path)."
    ),
    strict=False,
)
def test_fund_load_baseline_like_root_leaf_multiprocess_pipeline_e2e(tmp_path) -> None:
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    lifecycle_log_path = tmp_path / "lifecycle.log"
    runtime_log_path = tmp_path / "runtime_all.jsonl"
    line_count = 40

    input_path.write_text(
        "\n".join(_input_line(i) for i in range(1, line_count + 1)) + "\n",
        encoding="utf-8",
    )

    raw = load_yaml_config(Path("src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml"))
    config = validate_newgen_config(raw)

    adapters = config.get("adapters")
    assert isinstance(adapters, dict)
    source = adapters.get("source")
    sink = adapters.get("sink")
    assert isinstance(source, dict)
    assert isinstance(sink, dict)
    source_settings = source.get("settings")
    sink_settings = sink.get("settings")
    assert isinstance(source_settings, dict)
    assert isinstance(sink_settings, dict)
    source_settings["path"] = str(input_path)
    sink_settings["path"] = str(output_path)

    runtime = config.get("runtime")
    assert isinstance(runtime, dict)
    runtime["tracing"] = {"enabled": False}
    observability = runtime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    logging["exporters"] = [
        {
            "kind": "file_plain",
            "mode": "lifecycle",
            "settings": {"path": str(lifecycle_log_path)},
        },
        {
            "kind": "jsonl",
            "mode": "all",
            "settings": {"path": str(runtime_log_path)},
        },
    ]
    tracing = observability.get("tracing")
    if isinstance(tracing, dict):
        tracing["exporters"] = []
    monitoring = observability.get("monitoring")
    if isinstance(monitoring, dict):
        monitoring["exporters"] = []

    start_method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    ctx = mp.get_context(start_method)
    queue = ctx.Queue()
    process = ctx.Process(
        target=_run_runtime_config_subprocess,
        args=(config, queue),
        daemon=False,
        name="fund-load-multiprocess-e2e",
    )
    process.start()
    process.join(timeout=35.0)

    if process.is_alive():
        process.terminate()
        process.join(timeout=2.0)
        raise AssertionError(
            "run_with_config timed out (35s)\n"
            f"lifecycle_tail:\n{_tail(lifecycle_log_path)}\n"
            f"runtime_tail:\n{_tail(runtime_log_path)}"
        )

    result: dict[str, object] = {"status": "missing", "rc": process.exitcode}
    if not queue.empty():
        candidate = queue.get_nowait()
        if isinstance(candidate, dict):
            result = candidate

    if result.get("status") == "error":
        raise AssertionError(
            "run_with_config failed in subprocess\n"
            f"error={result.get('error')}\n"
            f"traceback={result.get('traceback')}\n"
            f"lifecycle_tail:\n{_tail(lifecycle_log_path)}\n"
            f"runtime_tail:\n{_tail(runtime_log_path)}"
        )

    assert int(result.get("rc", process.exitcode if process.exitcode is not None else -1)) == 0

    output_lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(output_lines) == line_count
    ids = [line.split('"')[3] for line in output_lines]
    assert ids == [str(i) for i in range(1, line_count + 1)]

    lifecycle_text = lifecycle_log_path.read_text(encoding="utf-8", errors="replace")
    assert "group 'execution.ingress'" in lifecycle_text
    assert "group 'execution.features'" in lifecycle_text
    assert "group 'execution.policy'" in lifecycle_text
    assert "group 'execution.egress'" in lifecycle_text
