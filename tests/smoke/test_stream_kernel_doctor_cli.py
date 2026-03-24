from __future__ import annotations

import json
import subprocess
import sys


def test_doctor_check_module_cli_json_output() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "stream_kernel.doctor",
            "check-module",
            "src/stream_kernel/adapters/file_io.py",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["adapter_decorated_targets"] >= 1

