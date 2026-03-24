from __future__ import annotations

from tools.framework_kb_mcp.fetch import fetch
from tools.framework_kb_mcp.search import search


def test_search_returns_docs_for_framework_query() -> None:
    result = search("routing service", scope="framework", top_k=3)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert "doc_id" in result[0]
    assert "snippet" in result[0]


def test_fetch_returns_exact_doc_content() -> None:
    payload = fetch("framework/initial_stage/release/agent_package/mcp_knowledge_contract")
    assert payload["doc_id"] == "framework/initial_stage/release/agent_package/mcp_knowledge_contract"
    content = payload["content"]
    assert isinstance(content, str)
    assert "search" in content
    assert "fetch" in content

