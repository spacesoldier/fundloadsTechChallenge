# MCP Knowledge Contract (Read-Only)

Goal: provide framework documentation context to agents without creating a second "answering agent".

## Tools

Only two required tools:

1. `search(query, scope, top_k)`:
- semantic/full-text search across local framework docs
- returns document ids, titles, scores, short snippets

2. `fetch(doc_id, section_ref)`:
- returns exact source excerpt for selected doc/section
- no rewriting, no interpretation

## Transport / Protocol

- MCP over `stdio` using JSON-RPC 2.0 messages framed with `Content-Length` headers.
- Server entrypoint:
  - `.venv/bin/python -m tools.framework_kb_mcp mcp-serve`
- Implemented methods:
  - `initialize`
  - `tools/list`
  - `tools/call`
  - `shutdown`
  - `exit`
- Supported tool names in `tools/call`:
  - `search`
  - `fetch`

## Response Shape

`search` response item:

```json
{
  "doc_id": "framework/architecture/runtime-model",
  "title": "Runtime Model",
  "score": 0.93,
  "snippet": "..."
}
```

`fetch` response:

```json
{
  "doc_id": "framework/architecture/runtime-model",
  "section_ref": "2.3",
  "content": "...exact text...",
  "source_path": "docs/framework/architecture/runtime-model.md"
}
```

## Operational Rules

1. MCP server is read-only.
2. No free-form "assistant answers" from MCP side.
3. Version lock to repo commit or release tag.
4. Return exact paths/sections for reproducibility.

## Suggested Source Sets

1. `docs/framework/**`
2. `docs/implementation/**`
3. `docs/guide/**`
4. `examples/golden/**`
5. `examples/anti_patterns/**`

## Reference Implementation

Local read-only implementation path:

```text
tools/framework_kb_mcp
```

Manual smoke check example:

```bash
.venv/bin/python -m pytest -q tests/smoke/test_framework_kb_mcp_mcp_stdio.py
```
