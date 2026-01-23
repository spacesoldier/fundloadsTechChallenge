from __future__ import annotations

from types import SimpleNamespace

# CLI overrides are applied in composition root startup per docs/implementation/kernel/Composition Root Spec.md.
from fund_load.app.cli import apply_tracing_overrides
from fund_load.usecases.config_models import AppConfig


def _config(with_tracing: bool) -> AppConfig:
    base = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "pipeline": {"steps": [{"name": "parse_load_attempt"}]},
        "policies": {
            "pack": "baseline",
            "evaluation_order": ["daily_attempt_limit"],
            "limits": {"daily_amount": "1.00", "weekly_amount": "1.00", "daily_attempts": 1},
        },
        "features": {
            "enabled": True,
            "monday_multiplier": {"enabled": False, "multiplier": "2.0", "apply_to": "amount"},
            "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": "9999.00"},
        },
        "windows": {
            "daily_attempts": {"enabled": True},
            "daily_accepted_amount": {"enabled": True},
            "weekly_accepted_amount": {"enabled": True},
            "daily_prime_gate": {"enabled": False},
        },
        "output": {"file": "output.txt"},
    }
    if with_tracing:
        base["tracing"] = {
            "enabled": False,
            "signature": {"mode": "type_only"},
            "context_diff": {"mode": "whitelist", "whitelist": ["line_no"]},
            "sink": {
                "kind": "jsonl",
                "jsonl": {"path": "trace.jsonl", "write_mode": "line", "flush_every_n": 1},
            },
        }
    return AppConfig.model_validate(base)


def test_apply_tracing_overrides_enable_and_path() -> None:
    # CLI flags must override config values (Composition Root spec §6.1).
    config = _config(with_tracing=True)
    args = SimpleNamespace(tracing="enable", trace_path="override.jsonl")

    apply_tracing_overrides(config, args)

    assert config.tracing is not None
    assert config.tracing.enabled is True
    assert config.tracing.sink is not None
    assert config.tracing.sink.jsonl is not None
    assert config.tracing.sink.jsonl.path == "override.jsonl"


def test_apply_tracing_overrides_disable() -> None:
    # Disable must override config even if enabled was true.
    config = _config(with_tracing=True)
    config.tracing.enabled = True
    args = SimpleNamespace(tracing="disable", trace_path=None)

    apply_tracing_overrides(config, args)

    assert config.tracing is not None
    assert config.tracing.enabled is False


def test_apply_tracing_overrides_creates_section_if_missing() -> None:
    # If tracing section is missing, CLI overrides should create it (Trace spec §9).
    config = _config(with_tracing=False)
    args = SimpleNamespace(tracing="enable", trace_path="trace.jsonl")

    apply_tracing_overrides(config, args)

    assert config.tracing is not None
    assert config.tracing.enabled is True
    assert config.tracing.sink is not None
    assert config.tracing.sink.kind == "jsonl"
