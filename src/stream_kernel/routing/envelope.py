from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Envelope:
    # Domain wrapper for routed messages (docs/framework/initial_stage/Routing semantics.md).
    payload: object
    trace_id: str | None = None
    target: str | Sequence[str] | None = None
    topic: str | None = None

    def __post_init__(self) -> None:
        # Invariants keep routing deterministic and explicit (Routing semantics §1–§2).
        if self.payload is None:
            raise ValueError("Envelope.payload must not be None")

        if self.trace_id is not None:
            if not isinstance(self.trace_id, str) or not self.trace_id:
                raise ValueError("Envelope.trace_id must be a non-empty string")

        if self.target is not None:
            if isinstance(self.target, str):
                if not self.target:
                    raise ValueError("Envelope.target must not be empty")
            else:
                # Reject empty target list to avoid "explicitly nowhere" routing.
                if len(self.target) == 0:
                    raise ValueError("Envelope.target list must not be empty")
                for item in self.target:
                    if not isinstance(item, str):
                        raise ValueError("Envelope.target entries must be strings")
                    if not item:
                        raise ValueError("Envelope.target entries must be non-empty")

        if self.topic is not None:
            if not isinstance(self.topic, str):
                raise ValueError("Envelope.topic must be a string")
            if not self.topic:
                raise ValueError("Envelope.topic must not be empty")
