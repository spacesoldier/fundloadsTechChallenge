from __future__ import annotations

import json
import re
from argparse import ArgumentParser
from pathlib import Path

from .catalog import resolve_doc_id


def fetch(
    doc_id: str,
    *,
    section_ref: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, object]:
    source = resolve_doc_id(doc_id, repo_root=repo_root)
    if source is None:
        raise ValueError(f"Unknown doc_id: {doc_id}")
    content = source.read_text(encoding="utf-8")
    if section_ref:
        content = _extract_section(content, section_ref)
    return {
        "doc_id": doc_id.strip("/"),
        "section_ref": section_ref,
        "content": content,
        "source_path": str(source),
    }


def _extract_section(content: str, section_ref: str) -> str:
    lines = content.splitlines()
    needle = section_ref.strip().lower()
    start = None
    level = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = re.sub(r"^#+\s*", "", stripped).strip().lower()
        if needle == heading or heading.startswith(needle):
            start = index
            level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start is None:
        return content
    end = len(lines)
    assert level is not None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        next_level = len(stripped) - len(stripped.lstrip("#"))
        if next_level <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _parser() -> ArgumentParser:
    parser = ArgumentParser(prog="framework-kb-fetch")
    parser.add_argument("doc_id")
    parser.add_argument("--section-ref", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = fetch(args.doc_id, section_ref=args.section_ref)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

