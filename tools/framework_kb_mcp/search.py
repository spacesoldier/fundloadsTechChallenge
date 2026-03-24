from __future__ import annotations

import json
import re
from argparse import ArgumentParser
from pathlib import Path

from .catalog import discover_documents


def search(
    query: str,
    *,
    scope: str | None = None,
    top_k: int = 5,
    repo_root: Path | None = None,
) -> list[dict[str, object]]:
    if not isinstance(query, str) or not query.strip():
        return []
    terms = _tokenize(query)
    if not terms:
        return []
    limited_k = max(1, int(top_k))
    candidates = []
    for doc in discover_documents(repo_root=repo_root):
        if scope and not doc.doc_id.startswith(scope.strip("/")):
            continue
        text = _read_text(doc.source_path)
        if not text:
            continue
        lowered = text.lower()
        score = sum(lowered.count(term) for term in terms)
        if " ".join(terms) in lowered:
            score += 2
        if score <= 0:
            continue
        snippet = _snippet(text=text, terms=terms)
        candidates.append(
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "score": float(score),
                "snippet": snippet,
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), str(item["doc_id"])))
    return candidates[:limited_k]


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_./-]+", text.lower()) if len(token) >= 2]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _snippet(*, text: str, terms: list[str], max_len: int = 220) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if any(term in lowered for term in terms):
            clean = line.strip()
            return clean[:max_len]
    compact = " ".join(text.split())
    return compact[:max_len]


def _parser() -> ArgumentParser:
    parser = ArgumentParser(prog="framework-kb-search")
    parser.add_argument("query")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = search(args.query, scope=args.scope, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

