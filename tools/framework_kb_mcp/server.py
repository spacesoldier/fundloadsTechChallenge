from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Any

from .fetch import fetch
from .search import search

_SERVER_NAME = "framework_kb_mcp"
_SERVER_VERSION = "0.1.0"
_PROTOCOL_VERSION = "2024-11-05"


def _parser() -> ArgumentParser:
    parser = ArgumentParser(prog="framework-kb-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

    search_cmd = sub.add_parser("search", help="Run read-only doc search")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--scope", default=None)
    search_cmd.add_argument("--top-k", type=int, default=5)

    fetch_cmd = sub.add_parser("fetch", help="Fetch exact doc section")
    fetch_cmd.add_argument("doc_id")
    fetch_cmd.add_argument("--section-ref", default=None)

    sub.add_parser("serve", help="Legacy JSONL request/response mode")
    sub.add_parser("mcp-serve", help="MCP stdio JSON-RPC mode")
    return parser


def serve() -> int:
    # Legacy JSONL "tool server":
    # input: {"tool":"search","args":{"query":"...","scope":"...","top_k":5}}
    # input: {"tool":"fetch","args":{"doc_id":"...","section_ref":"..."}}
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            payload = _call_tool(
                name=request.get("tool"),
                arguments=request.get("args", {}),
            )
            response = {"ok": True, "result": payload}
        except Exception as exc:  # noqa: BLE001
            response = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


@dataclass(slots=True)
class _McpState:
    shutdown_requested: bool = False
    exit_requested: bool = False


def mcp_serve() -> int:
    state = _McpState()
    while True:
        request = _read_jsonrpc_message()
        if request is None:
            break
        response = _handle_mcp_message(request, state=state)
        if response is not None:
            _write_jsonrpc_message(response)
        if state.exit_requested:
            break
    return 0


def _handle_mcp_message(message: dict[str, object], *, state: _McpState) -> dict[str, object] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not method:
        return _error_response(request_id, code=-32600, message="Invalid Request")

    # Notifications do not require responses.
    if method in {"notifications/initialized", "initialized", "$/cancelRequest"}:
        return None

    if method == "initialize":
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        }
        return _ok_response(request_id, result)

    if method == "tools/list":
        result = {"tools": _tools_list()}
        return _ok_response(request_id, result)

    if method == "tools/call":
        if not isinstance(params, dict):
            return _error_response(request_id, code=-32602, message="Invalid params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            payload = _call_tool(name=name, arguments=arguments)
            result = {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "structuredContent": payload,
                "isError": False,
            }
            return _ok_response(request_id, result)
        except Exception as exc:  # noqa: BLE001
            result = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
            return _ok_response(request_id, result)

    if method == "shutdown":
        state.shutdown_requested = True
        return _ok_response(request_id, {})

    if method == "exit":
        state.exit_requested = True
        return None

    return _error_response(request_id, code=-32601, message=f"Method not found: {method}")


def _tools_list() -> list[dict[str, object]]:
    return [
        {
            "name": "search",
            "description": "Read-only search across local framework docs",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {"type": ["string", "null"]},
                    "top_k": {"type": "integer", "minimum": 1, "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "fetch",
            "description": "Read exact source excerpt for selected document/section",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "section_ref": {"type": ["string", "null"]},
                },
                "required": ["doc_id"],
            },
        },
    ]


def _call_tool(*, name: object, arguments: object) -> object:
    if not isinstance(name, str) or not name:
        raise ValueError("tool name must be a non-empty string")
    args = arguments if isinstance(arguments, dict) else {}
    if name == "search":
        query = str(args.get("query", ""))
        scope = args.get("scope")
        top_k = int(args.get("top_k", 5))
        return search(query=query, scope=scope if isinstance(scope, str) else None, top_k=top_k)
    if name == "fetch":
        doc_id = str(args.get("doc_id", ""))
        section_ref = args.get("section_ref")
        return fetch(doc_id=doc_id, section_ref=section_ref if isinstance(section_ref, str) else None)
    raise ValueError(f"Unsupported tool: {name}")


def _ok_response(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: object, *, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _read_jsonrpc_message() -> dict[str, object] | None:
    stream = sys.stdin.buffer
    first_line = stream.readline()
    if not first_line:
        return None

    # Legacy line-delimited JSON fallback (useful for manual tests).
    stripped = first_line.strip()
    if stripped.startswith(b"{"):
        return _loads_json(stripped)

    headers: dict[str, str] = {}
    line = first_line
    while line:
        if line in {b"\r\n", b"\n"}:
            break
        raw = line.decode("utf-8", errors="replace").strip()
        if ":" in raw:
            key, value = raw.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        line = stream.readline()

    length_raw = headers.get("content-length")
    if not length_raw:
        return None
    length = int(length_raw)
    payload = stream.read(length)
    if not payload:
        return None
    return _loads_json(payload)


def _write_jsonrpc_message(message: dict[str, object]) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(payload)}\r\nContent-Type: application/json\r\n\r\n".encode(
        "ascii"
    )
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _loads_json(data: bytes) -> dict[str, object]:
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON-RPC payload must be an object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "search":
        result = search(args.query, scope=args.scope, top_k=args.top_k)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fetch":
        result = fetch(args.doc_id, section_ref=args.section_ref)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        return serve()
    if args.command == "mcp-serve":
        return mcp_serve()
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

