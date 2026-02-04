from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from stream_kernel.application_context import ApplicationContext, ContextBuildError


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_auto_discover_scans_all_modules_under_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Auto-discovery should scan all modules under root and find annotations.
    pkg = tmp_path / "fakeapp"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "from stream_kernel.kernel.node import node\n"
        "@node(name='a')\n"
        "class A:\n"
        "    def __call__(self, msg, ctx):\n"
        "        return [msg]\n",
    )
    _write_file(pkg / "plain.py", "x = 1\n")

    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("APP_CONTEXT_ROOT", "fakeapp")

    ctx = ApplicationContext()
    ctx.auto_discover()
    names = [n.meta.name for n in ctx.nodes]
    assert names == ["a"]

    sys.path.remove(str(tmp_path))


def test_auto_discover_raises_on_missing_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Missing root package must fail fast.
    monkeypatch.setenv("APP_CONTEXT_ROOT", "missing_app")
    ctx = ApplicationContext()
    with pytest.raises(ContextBuildError):
        ctx.auto_discover()


def test_auto_discover_requires_env_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # APP_CONTEXT_ROOT must be set to enable auto-discovery (Auto-discovery policy).
    monkeypatch.delenv("APP_CONTEXT_ROOT", raising=False)
    ctx = ApplicationContext()
    with pytest.raises(ContextBuildError):
        ctx.auto_discover()


def test_auto_discover_rejects_non_package_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Root must be a package with __path__ (Auto-discovery policy).
    monkeypatch.setenv("APP_CONTEXT_ROOT", "stream_kernel.kernel.node")
    ctx = ApplicationContext()
    with pytest.raises(ContextBuildError):
        ctx.auto_discover()


def test_auto_discover_raises_on_import_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Import errors inside the package should surface as ContextBuildError (Auto-discovery policy).
    pkg = tmp_path / "fakeapp_err"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "bad.py", "raise RuntimeError('boom')\n")

    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("APP_CONTEXT_ROOT", "fakeapp_err")

    ctx = ApplicationContext()
    with pytest.raises(ContextBuildError):
        ctx.auto_discover()

    sys.path.remove(str(tmp_path))

def test_auto_discover_excludes_module_prefixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Excluded module prefixes should be skipped during scanning.
    pkg = tmp_path / "fakeapp2"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "nodes.py",
        "from stream_kernel.kernel.node import node\n"
        "@node(name='a')\n"
        "class A:\n"
        "    def __call__(self, msg, ctx):\n"
        "        return [msg]\n",
    )
    _write_file(
        pkg / "skipme.py",
        "from stream_kernel.kernel.node import node\n"
        "@node(name='b')\n"
        "class B:\n"
        "    def __call__(self, msg, ctx):\n"
        "        return [msg]\n",
    )

    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("APP_CONTEXT_ROOT", "fakeapp2")
    monkeypatch.setenv("APP_CONTEXT_EXCLUDE", "fakeapp2.skipme")

    ctx = ApplicationContext()
    ctx.auto_discover()
    names = [n.meta.name for n in ctx.nodes]
    assert names == ["a"]

    sys.path.remove(str(tmp_path))
