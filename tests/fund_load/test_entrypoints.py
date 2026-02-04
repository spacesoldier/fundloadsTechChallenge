from __future__ import annotations

import runpy

import pytest

import fund_load.main as main_module


def test_main_delegates_to_framework_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Entrypoint delegation is documented in docs/implementation/architecture/Architecture overview.md.
    seen: dict[str, object] = {}

    def _fake_run(argv: list[str] | None) -> int:
        seen["argv"] = argv
        return 7

    monkeypatch.setattr(main_module, "run", _fake_run)

    exit_code = main_module.main(["--config", "cfg.yml"])

    assert exit_code == 7
    assert seen["argv"] == ["--config", "cfg.yml"]


def test_module_main_exits_with_code(monkeypatch: pytest.MonkeyPatch) -> None:
    # __main__ should translate the main() return code into a SystemExit (CLI contract).
    monkeypatch.setattr(main_module, "main", lambda _argv=None: 3)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("fund_load", run_name="__main__")

    assert excinfo.value.code == 3
