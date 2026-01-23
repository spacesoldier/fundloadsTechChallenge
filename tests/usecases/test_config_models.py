from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

# Config models are based on docs/implementation/architecture/Configuration spec.md.
from fund_load.usecases.config_models import AppConfig


def test_config_models_accept_minimal_sections() -> None:
    # Each required section is validated and parsed into a typed model.
    cfg = AppConfig.model_validate(
        {
            "version": 1,
            "scenario": {"name": "baseline"},
            "pipeline": {"steps": [{"name": "parse_load_attempt"}]},
            "policies": {
                "pack": "baseline",
                "evaluation_order": [
                    "daily_attempt_limit",
                    "daily_amount_limit",
                    "weekly_amount_limit",
                ],
                "limits": {
                    "daily_amount": "5000.00",
                    "weekly_amount": "20000.00",
                    "daily_attempts": 3,
                },
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
    )
    assert cfg.output.file_path == "output.txt"
    assert cfg.policies.limits.daily_amount == Decimal("5000.00")


def test_config_models_reject_unknown_keys() -> None:
    # Unknown keys are rejected to fail fast on misconfiguration.
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
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
                    "unknown": 123,
                },
                "windows": {
                    "daily_attempts": {"enabled": True},
                    "daily_accepted_amount": {"enabled": True},
                    "weekly_accepted_amount": {"enabled": True},
                    "daily_prime_gate": {"enabled": False},
                },
                "output": {"file": "output.txt"},
            }
        )


def test_output_config_accepts_file_path_aliases() -> None:
    # Docs conflict: Configuration spec uses output.file, WriteOutput spec uses output.file_path.
    # We accept both, normalizing to file_path in the model.
    cfg = AppConfig.model_validate(
        {
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
            "output": {"file_path": "output.txt"},
        }
    )
    assert cfg.output.file_path == "output.txt"


def test_windows_config_accepts_prime_gate_alias() -> None:
    # Docs conflict: Configuration spec mentions prime_daily_global_gate, UpdateWindows uses daily_prime_gate.
    # We accept both, normalizing to daily_prime_gate in the model.
    cfg = AppConfig.model_validate(
        {
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
                "prime_daily_global_gate": {"enabled": False},
            },
            "output": {"file": "output.txt"},
        }
    )
    assert cfg.windows.daily_prime_gate.enabled is False


def test_tracing_config_accepts_jsonl_sink() -> None:
    # Trace config follows docs/implementation/kernel/Trace and Context Change Log Spec.md.
    cfg = AppConfig.model_validate(
        {
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
            "tracing": {
                "enabled": True,
                "signature": {"mode": "type_only"},
                "context_diff": {"mode": "whitelist", "whitelist": ["line_no", "tags"]},
                "sink": {
                    "kind": "jsonl",
                    "jsonl": {
                        "path": "var/trace.jsonl",
                        "write_mode": "line",
                        "flush_every_n": 1,
                        "fsync_every_n": None,
                    },
                },
            },
        }
    )
    assert cfg.tracing is not None
    assert cfg.tracing.sink.kind == "jsonl"
    assert cfg.tracing.sink.jsonl is not None
    assert cfg.tracing.context_diff.whitelist == ["line_no", "tags"]


def test_tracing_config_requires_jsonl_section_for_jsonl_kind() -> None:
    # For jsonl sink, jsonl section is mandatory to avoid ambiguous defaults.
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
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
                "tracing": {
                    "enabled": True,
                    "signature": {"mode": "type_only"},
                    "context_diff": {"mode": "none"},
                    "sink": {"kind": "jsonl"},
                },
            }
        )
