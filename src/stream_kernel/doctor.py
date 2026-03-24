from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CheckModuleReport:
    path: str
    node_decorated_targets: int
    service_decorated_targets: int
    adapter_decorated_targets: int
    violations: list[str]

    @property
    def ok(self) -> bool:
        return len(self.violations) == 0


def check_module(module_or_path: str) -> CheckModuleReport:
    path = _resolve_module_path(module_or_path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    node_targets = 0
    service_targets = 0
    adapter_targets = 0
    violations: list[str] = []

    for stmt in tree.body:
        decorators = _decorator_names(stmt)
        if "node" in decorators:
            node_targets += 1
            if isinstance(stmt, ast.ClassDef) and not _has_call_method(stmt):
                violations.append(f"{stmt.name}: @node class must define __call__")
        if "service" in decorators:
            service_targets += 1
            if not isinstance(stmt, ast.ClassDef):
                violations.append(f"{_name_of(stmt)}: @service must decorate a class")
        if "adapter" in decorators:
            adapter_targets += 1
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                violations.append(f"{_name_of(stmt)}: @adapter must decorate a function")

    return CheckModuleReport(
        path=str(path),
        node_decorated_targets=node_targets,
        service_decorated_targets=service_targets,
        adapter_decorated_targets=adapter_targets,
        violations=violations,
    )


def _resolve_module_path(module_or_path: str) -> Path:
    candidate = Path(module_or_path)
    if candidate.exists() and candidate.is_file():
        return candidate.resolve()
    spec = importlib.util.find_spec(module_or_path)
    if spec is None or spec.origin is None:
        raise ValueError(f"Cannot resolve module or path: {module_or_path}")
    return Path(spec.origin).resolve()


def _decorator_names(stmt: ast.stmt) -> set[str]:
    if not isinstance(
        stmt,
        (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
    ):
        return set()
    names: set[str] = set()
    for dec in stmt.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _has_call_method(cls: ast.ClassDef) -> bool:
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__call__":
            return True
    return False


def _name_of(stmt: ast.stmt) -> str:
    return getattr(stmt, "name", stmt.__class__.__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stream-kernel-doctor")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("check-module")
    cmd.add_argument("module_or_path")
    cmd.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check-module":
        report = check_module(args.module_or_path)
        payload = {
            "ok": report.ok,
            "path": report.path,
            "node_decorated_targets": report.node_decorated_targets,
            "service_decorated_targets": report.service_decorated_targets,
            "adapter_decorated_targets": report.adapter_decorated_targets,
            "violations": report.violations,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"ok={payload['ok']} "
                f"nodes={payload['node_decorated_targets']} "
                f"services={payload['service_decorated_targets']} "
                f"adapters={payload['adapter_decorated_targets']}"
            )
            for violation in report.violations:
                print(f"- {violation}")
        return 0 if report.ok else 1
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

