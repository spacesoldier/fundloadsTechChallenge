from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class CtxError:
    # Structured errors are defined in docs/implementation/kernel/Context Spec.md.
    code: str
    message: str
    step: str | None = None
    phase: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Context:
    # Context is mutable runtime metadata per Context Spec (not domain state).
    trace_id: str
    run_id: str
    scenario_id: str
    received_at: datetime
    tags: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[CtxError] = field(default_factory=list)
    trace: list[object] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)

    def tag(self, key: str, value: str) -> None:
        # Enforce string values for tags (Context Spec).
        if not isinstance(value, str):
            raise TypeError("Context.tag value must be str")
        self.tags[key] = value

    def metric_set(self, key: str, value: float | int) -> None:
        # Enforce numeric values for metrics (Context Spec).
        if not isinstance(value, (float, int)):
            raise TypeError("Context.metric_set value must be numeric")
        self.metrics[key] = float(value)

    def note(self, text: str) -> None:
        # Notes are append-only in order of occurrence.
        self.notes.append(text)

    def error(self, code: str, message: str, *, step: str | None = None, details: dict[str, object] | None = None) -> None:
        # Errors are structured records for traceability.
        self.errors.append(
            CtxError(
                code=code,
                message=message,
                step=step,
                details={} if details is None else details,
            )
        )

    def set_flag(self, name: str, value: bool = True) -> None:
        # Flags are boolean toggles (Context Spec).
        self.flags[name] = value

    def is_flag(self, name: str) -> bool:
        return self.flags.get(name, False)


@dataclass(frozen=True, slots=True)
class ContextFactory:
    # ContextFactory owns per-event Context creation (Context Spec).
    run_id: str
    scenario_id: str

    def new(self) -> Context:
        # Trace id is generated per event; can be swapped for deterministic generator later.
        trace_id = uuid.uuid4().hex
        return Context(
            trace_id=trace_id,
            run_id=self.run_id,
            scenario_id=self.scenario_id,
            received_at=datetime.now(tz=UTC),
        )
