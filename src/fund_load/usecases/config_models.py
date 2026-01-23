from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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


class TraceSignatureConfig(BaseModel):
    # Signature config for TraceRecorder (Trace and Context Change Log spec).
    model_config = ConfigDict(extra="forbid")
    mode: Literal["type_only", "type_and_identity", "hash"] = "type_only"


class TraceContextDiffConfig(BaseModel):
    # Context diff config controls ctx snapshotting (Trace spec).
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "whitelist", "debug"] = "none"
    whitelist: list[str] = Field(default_factory=list)


class TraceSinkJsonlConfig(BaseModel):
    # Jsonl sink configuration (Trace spec §7).
    model_config = ConfigDict(extra="forbid")
    path: str
    write_mode: Literal["line", "batch"] = "line"
    flush_every_n: int = 1
    flush_every_ms: int | None = None
    fsync_every_n: int | None = None


class TraceSinkConfig(BaseModel):
    # Trace sink selector: only one sink is active at a time (Trace spec §6/9).
    model_config = ConfigDict(extra="forbid")
    kind: Literal["jsonl", "stdout", "otel"]
    jsonl: TraceSinkJsonlConfig | None = None

    @model_validator(mode="after")
    def _require_jsonl(self) -> TraceSinkConfig:
        # For jsonl kind, a jsonl section is required to avoid silent defaults.
        if self.kind == "jsonl" and self.jsonl is None:
            raise ValueError("tracing.sink.jsonl is required when kind is 'jsonl'")
        return self


class TracingConfig(BaseModel):
    # Top-level tracing configuration (Trace spec §9).
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    signature: TraceSignatureConfig = Field(default_factory=TraceSignatureConfig)
    context_diff: TraceContextDiffConfig = Field(default_factory=TraceContextDiffConfig)
    sink: TraceSinkConfig | None = None


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
    tracing: TracingConfig | None = None
