from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from fund_load.domain.messages import IdemStatus
from fund_load.usecases.messages import AttemptWithKeys, IdempotencyClassifiedAttempt
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/03 IdempotencyGate.md).
@node(name="idempotency_gate")
@dataclass
class IdempotencyGate:
    # Step 03 enforces deterministic replay/conflict classification (docs/implementation/steps/03 IdempotencyGate.md).
    _registry: dict[str, tuple[str, int]] = field(default_factory=dict)

    def __call__(
        self, msg: AttemptWithKeys, ctx: object | None
    ) -> list[IdempotencyClassifiedAttempt]:
        fingerprint = _fingerprint_for(msg)
        id_value = msg.attempt.id

        stored = self._registry.get(id_value)
        if stored is None:
            # In-memory registry is used for the challenge; this can be externalized (e.g., Redis)
            # behind a port to support shared/durable idempotency state.
            self._registry[id_value] = (fingerprint, msg.attempt.line_no)
            return [
                IdempotencyClassifiedAttempt(
                    base=msg,
                    idem_status=IdemStatus.CANONICAL,
                    fingerprint=fingerprint,
                    canonical_line_no=msg.attempt.line_no,
                )
            ]

        canonical_fp, canonical_line_no = stored
        if fingerprint == canonical_fp:
            status = IdemStatus.DUP_REPLAY
        else:
            status = IdemStatus.DUP_CONFLICT

        # When using an external store (e.g., Redis), this read path would be a port call.
        return [
            IdempotencyClassifiedAttempt(
                base=msg,
                idem_status=status,
                fingerprint=fingerprint,
                canonical_line_no=canonical_line_no,
            )
        ]


def _fingerprint_for(msg: AttemptWithKeys) -> str:
    # Fingerprint excludes id per input analysis doc; use normalized fields only.
    canonical = (
        f"{msg.attempt.customer_id}|"
        f"{msg.attempt.amount.amount}|"
        f"{msg.attempt.ts.isoformat()}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
