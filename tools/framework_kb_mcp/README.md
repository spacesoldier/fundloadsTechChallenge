# framework_kb_mcp (Local Read-Only Knowledge Tools)

Reference implementation of the `search`/`fetch` contract from:
- `docs/framework/initial_stage/release/agent_package/mcp_knowledge_contract.md`

## Commands

Search:

```bash
.venv/bin/python -m tools.framework_kb_mcp search "node service store" --scope framework/initial_stage --top-k 5
```

Fetch:

```bash
.venv/bin/python -m tools.framework_kb_mcp fetch framework/initial_stage/release/agent_package/mcp_knowledge_contract
```

JSONL serve mode:

```bash
.venv/bin/python -m tools.framework_kb_mcp serve
```

Request example:

```json
{"tool":"search","args":{"query":"ring pipeline","scope":"framework","top_k":3}}
```

MCP stdio mode (JSON-RPC + `Content-Length` framing):

```bash
.venv/bin/python -m tools.framework_kb_mcp mcp-serve
```

Supported MCP methods:

- `initialize`
- `tools/list`
- `tools/call` (`search`, `fetch`)
- `shutdown`
- `exit`

## Rules

- Read-only: no write operations.
- Returns exact source excerpts for `fetch`.
- Uses local repository docs as source of truth.
