from __future__ import annotations

import json
import subprocess
import sys


def _write_framed(proc: subprocess.Popen[bytes], payload: dict[str, object]) -> None:
    assert proc.stdin is not None
    raw = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header + raw)
    proc.stdin.flush()


def _read_framed(proc: subprocess.Popen[bytes]) -> dict[str, object]:
    assert proc.stdout is not None
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("EOF while reading MCP headers")
        if line in {b"\r\n", b"\n"}:
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    payload = proc.stdout.read(length)
    return json.loads(payload.decode("utf-8"))


def test_mcp_stdio_initialize_list_and_call() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "tools.framework_kb_mcp", "mcp-serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _write_framed(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        init_resp = _read_framed(proc)
        assert init_resp["id"] == 1
        assert init_resp["result"]["serverInfo"]["name"] == "framework_kb_mcp"

        _write_framed(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools_resp = _read_framed(proc)
        names = [item["name"] for item in tools_resp["result"]["tools"]]
        assert "search" in names
        assert "fetch" in names

        _write_framed(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": "routing service", "scope": "framework", "top_k": 2},
                },
            },
        )
        call_resp = _read_framed(proc)
        assert call_resp["id"] == 3
        assert call_resp["result"]["isError"] is False
        assert call_resp["result"]["structuredContent"]

        _write_framed(
            proc,
            {"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}},
        )
        shutdown_resp = _read_framed(proc)
        assert shutdown_resp["id"] == 4
        assert shutdown_resp["result"] == {}

        _write_framed(
            proc,
            {"jsonrpc": "2.0", "method": "exit", "params": {}},
        )
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        proc.wait(timeout=5)

