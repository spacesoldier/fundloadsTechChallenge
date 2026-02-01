from __future__ import annotations

from decimal import Decimal
from typing import Any

from stream_kernel.kernel.step_registry import StepRegistry
from fund_load.usecases.config_models import AppConfig
from fund_load.usecases.steps import (
    ComputeFeatures,
    ComputeTimeKeys,
    EvaluatePolicies,
    FormatOutput,
    IdempotencyGate,
    ParseLoadAttempt,
    UpdateWindows,
    WriteOutput,
)


def build_step_registry(config: AppConfig, wiring: dict[str, object]) -> StepRegistry:
    # Step registry is built from config + wiring (ScenarioBuilder/Composition Root spec).
    registry = StepRegistry()

    registry.register("parse_load_attempt", lambda cfg, w: ParseLoadAttempt())

    # Docs mention configurable week_start; we default to MON until domain/time config is modeled.
    registry.register(
        "compute_time_keys",
        lambda cfg, w: ComputeTimeKeys(week_start=cfg.get("week_start", "MON")),
    )

    registry.register("idempotency_gate", lambda cfg, w: IdempotencyGate())

    registry.register(
        "compute_features",
        lambda cfg, w: ComputeFeatures(
            monday_multiplier_enabled=config.features.monday_multiplier.enabled,
            monday_multiplier=config.features.monday_multiplier.multiplier,
            apply_to=config.features.monday_multiplier.apply_to,
            prime_checker=_require(w, "prime_checker"),
            prime_enabled=config.features.prime_gate.enabled,
        ),
    )

    # Policy config uses policies.prime_gate when present; otherwise fall back to features.prime_gate.
    # This resolves ambiguity between config sections in docs (Configuration spec vs Step 05 spec).
    prime_cfg = config.policies.prime_gate or config.features.prime_gate

    registry.register(
        "evaluate_policies",
        lambda cfg, w: EvaluatePolicies(
            window_store=_require(w, "window_store"),
            daily_attempt_limit=config.policies.limits.daily_attempts,
            daily_amount_limit=config.policies.limits.daily_amount,
            weekly_amount_limit=config.policies.limits.weekly_amount,
            prime_enabled=prime_cfg.enabled,
            prime_amount_cap=prime_cfg.amount_cap,
            prime_global_per_day=prime_cfg.global_per_day,
        ),
    )

    registry.register(
        "update_windows",
        lambda cfg, w: UpdateWindows(
            window_store=_require(w, "window_store"),
            prime_gate_enabled=config.windows.daily_prime_gate.enabled,
        ),
    )

    registry.register("format_output", lambda cfg, w: FormatOutput())

    registry.register(
        "write_output",
        lambda cfg, w: WriteOutput(output_sink=_require(w, "output_sink")),
    )

    return registry


def _require(wiring: dict[str, object], key: str) -> Any:
    # Wiring must provide required ports; raise KeyError to fail fast.
    if key not in wiring:
        raise KeyError(f"Missing wiring dependency: {key}")
    return wiring[key]
