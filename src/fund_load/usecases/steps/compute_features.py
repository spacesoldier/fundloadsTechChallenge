from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from fund_load.domain.money import Money
from fund_load.ports.prime_checker import PrimeChecker
from fund_load.usecases.messages import EnrichedAttempt, Features, IdempotencyClassifiedAttempt
from stream_kernel.application_context.config_inject import config
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/04 ComputeFeatures.md).
# consumes/emits are used for DAG construction (docs/framework/initial_stage/DAG construction.md).
@node(name="compute_features", consumes=[IdempotencyClassifiedAttempt], emits=[EnrichedAttempt])
@dataclass(frozen=True, slots=True)
class ComputeFeatures:
    # Step 04 computes risk_factor/effective_amount/is_prime_id (docs/implementation/steps/04 ComputeFeatures.md).
    # Config-driven fields come from nodes.compute_features.* (newgen config).
    monday_multiplier_enabled: bool = config.value("monday_multiplier.enabled", default=False)
    monday_multiplier: Decimal = config.value("monday_multiplier.multiplier", default=Decimal("1"))
    apply_to: str = config.value("monday_multiplier.apply_to", default="amount")
    prime_enabled: bool = config.value("prime_gate.enabled", default=False)
    # Dependency is injected by type: PrimeChecker is bound in the injection registry.
    # We use the generic "kv" port_type as a service bucket in this initial stage.
    prime_checker: PrimeChecker = inject.kv(PrimeChecker)

    def __call__(self, msg: IdempotencyClassifiedAttempt, ctx: object | None) -> list[EnrichedAttempt]:
        # Monday detection uses UTC timestamp per Time and Money Semantics doc.
        risk_factor = self._risk_factor(msg.base.attempt.ts)
        effective_amount = self._effective_amount(msg.base.attempt.amount, risk_factor)
        is_prime = self._is_prime(msg.base.attempt.id)
        features = Features(
            risk_factor=risk_factor,
            effective_amount=effective_amount,
            is_prime_id=is_prime,
        )
        return [EnrichedAttempt(base=msg, features=features)]

    def _risk_factor(self, ts: datetime) -> Decimal:
        # Monday is weekday=0 in Python (UTC-aligned per Step 04).
        if not self.monday_multiplier_enabled:
            return Decimal("1")
        if ts.weekday() == 0:
            return self.monday_multiplier
        return Decimal("1")

    def _effective_amount(self, amount: Money, risk_factor: Decimal) -> Money:
        # Default semantics apply multiplier to amount; "limits" mode keeps amount unchanged.
        if self.apply_to == "amount":
            return Money(currency=amount.currency, amount=amount.amount * risk_factor)
        if self.apply_to == "limits":
            return Money(currency=amount.currency, amount=amount.amount)
        raise ValueError("apply_to must be 'amount' or 'limits'")

    def _is_prime(self, id_value: str) -> bool:
        # Prime gate is optional; disabled => always false (Step 04).
        if not self.prime_enabled:
            return False
        return self.prime_checker.is_prime(int(id_value))
