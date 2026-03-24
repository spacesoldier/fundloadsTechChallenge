from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    doc_id: str
    title: str
    source_path: Path


def default_repo_root() -> Path:
    # tools/framework_kb_mcp -> repo root
    return Path(__file__).resolve().parents[2]


def default_source_roots(repo_root: Path | None = None) -> list[Path]:
    root = repo_root if isinstance(repo_root, Path) else default_repo_root()
    return [
        root / "docs" / "framework",
        root / "docs" / "implementation",
        root / "docs" / "guide",
        root
        / "docs"
        / "framework"
        / "initial_stage"
        / "release"
        / "agent_package"
        / "examples"
        / "golden",
        root
        / "docs"
        / "framework"
        / "initial_stage"
        / "release"
        / "agent_package"
        / "examples"
        / "anti_patterns",
    ]


def discover_documents(
    *,
    repo_root: Path | None = None,
    roots: list[Path] | None = None,
) -> list[KnowledgeDocument]:
    root = repo_root if isinstance(repo_root, Path) else default_repo_root()
    source_roots = roots if isinstance(roots, list) and roots else default_source_roots(root)
    docs: list[KnowledgeDocument] = []
    seen: set[Path] = set()
    for base in source_roots:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            if path in seen:
                continue
            seen.add(path)
            doc_id = _doc_id_for_path(path=path, repo_root=root)
            docs.append(
                KnowledgeDocument(
                    doc_id=doc_id,
                    title=_title_for_markdown(path),
                    source_path=path,
                )
            )
    return docs


def resolve_doc_id(doc_id: str, *, repo_root: Path | None = None) -> Path | None:
    if not isinstance(doc_id, str) or not doc_id.strip():
        return None
    normalized = doc_id.strip().strip("/")
    root = repo_root if isinstance(repo_root, Path) else default_repo_root()
    candidate = (root / "docs" / f"{normalized}.md").resolve()
    if candidate.exists() and candidate.is_file():
        return candidate
    for doc in discover_documents(repo_root=root):
        if doc.doc_id == normalized:
            return doc.source_path
    return None


def _doc_id_for_path(*, path: Path, repo_root: Path) -> str:
    rel = path.resolve().relative_to((repo_root / "docs").resolve())
    return str(rel.with_suffix("")).replace("\\", "/")


def _title_for_markdown(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return path.stem

