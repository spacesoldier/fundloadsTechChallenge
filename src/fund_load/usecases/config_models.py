from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# Config models map YAML sections to typed structures (docs/implementation/architecture/Configuration spec.md).


class StepDecl(BaseModel):
    # Step declaration mirrors pipeline.steps entries and uses "config" to align with ScenarioBuilder spec.
    model_config = ConfigDict(extra="forbid")
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    # Pipeline configuration holds the ordered step list.
    model_config = ConfigDict(extra="forbid")
    steps: list[StepDecl]


class LimitsConfig(BaseModel):
    # Limits are currency/attempt thresholds used by policy evaluation.
    model_config = ConfigDict(extra="forbid")
    daily_amount: Decimal
    weekly_amount: Decimal
    daily_attempts: int


class PoliciesPrimeGateConfig(BaseModel):
    # Prime gate policy settings (Step 05 spec).
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    global_per_day: int = 1
    amount_cap: Decimal = Decimal("9999.00")


class PoliciesConfig(BaseModel):
    # Policies section defines pack, evaluation order, and limits.
    model_config = ConfigDict(extra="forbid")
    pack: str
    evaluation_order: list[str]
    limits: LimitsConfig
    prime_gate: PoliciesPrimeGateConfig | None = None


class MondayMultiplierConfig(BaseModel):
    # Monday multiplier config matches Step 04 semantics.
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    multiplier: Decimal
    apply_to: str


class PrimeGateFeatureConfig(BaseModel):
    # Prime gate feature config is used by ComputeFeatures and policies.
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    global_per_day: int
    amount_cap: Decimal


class FeaturesConfig(BaseModel):
    # Feature toggles and parameters (Step 04).
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    monday_multiplier: MondayMultiplierConfig
    prime_gate: PrimeGateFeatureConfig


class WindowToggle(BaseModel):
    # Window toggles enable/disable window tracking.
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class WindowsConfig(BaseModel):
    # Window configuration for UpdateWindows and policies.
    model_config = ConfigDict(extra="forbid")
    daily_attempts: WindowToggle
    daily_accepted_amount: WindowToggle
    weekly_accepted_amount: WindowToggle
    # Docs conflict: Configuration spec mentions prime_daily_global_gate vs UpdateWindows daily_prime_gate.
    # We accept both names and normalize to daily_prime_gate.
    daily_prime_gate: WindowToggle = Field(
        validation_alias=AliasChoices("daily_prime_gate", "prime_daily_global_gate")
    )


class OutputConfig(BaseModel):
    # Output configuration for WriteOutput.
    model_config = ConfigDict(extra="forbid")
    # Docs conflict: Configuration spec uses output.file, WriteOutput spec uses output.file_path.
    # We accept both names and normalize to file_path.
    file_path: str = Field(validation_alias=AliasChoices("file_path", "file"))


class ScenarioConfig(BaseModel):
    # Scenario config identifies the flow to build (Configuration spec).
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str | None = None


class AppConfig(BaseModel):
    # AppConfig is the top-level typed view of configuration.
    model_config = ConfigDict(extra="forbid")
    version: int
    scenario: ScenarioConfig
    pipeline: PipelineConfig
    policies: PoliciesConfig
    features: FeaturesConfig
    windows: WindowsConfig
    output: OutputConfig
