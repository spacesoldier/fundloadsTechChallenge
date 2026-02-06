from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fund_load.domain.messages import IdemStatus
from fund_load.domain.reasons import ReasonCode
from fund_load.ports.window_store import WindowReadPort
from fund_load.usecases.messages import Decision, EnrichedAttempt
from stream_kernel.application_context.config_inject import config
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/05 EvaluatePolicies.md).
# consumes/emits are used for DAG construction (docs/framework/initial_stage/DAG construction.md).
@node(name="evaluate_policies", consumes=[EnrichedAttempt], emits=[Decision])
@dataclass(frozen=True, slots=True)
class EvaluatePolicies:
    # Step 05 applies policy order and produces Decision (docs/implementation/steps/05 EvaluatePolicies.md).
    # Dependency injection: WindowStore read port is provided by the runtime wiring.
    # We use the generic "kv" port_type as a service bucket in this initial stage.
    window_store: WindowReadPort = inject.kv(WindowReadPort)
    # Config-driven limits come from nodes.evaluate_policies.* (newgen config).
    daily_attempt_limit: int = config.value("limits.daily_attempts", default=0)
    daily_amount_limit: Decimal = config.value("limits.daily_amount", default=Decimal("0"))
    weekly_amount_limit: Decimal = config.value("limits.weekly_amount", default=Decimal("0"))
    prime_enabled: bool = config.value("prime_gate.enabled", default=False)
    prime_amount_cap: Decimal = config.value("prime_gate.amount_cap", default=Decimal("0"))
    prime_global_per_day: int = config.value("prime_gate.global_per_day", default=0)

    def __call__(self, msg: EnrichedAttempt, ctx: object | None) -> list[Decision]:
        # Snapshot reads must reflect prior updates; WindowStore port guarantees this ordering.
        snapshot = self.window_store.read_snapshot(
            customer_id=msg.base.base.attempt.customer_id,
            day_key=msg.base.base.day_key,
            week_key=msg.base.base.week_key.week_start_date,
        )

        idem_status = msg.base.idem_status
        is_canonical = idem_status == IdemStatus.CANONICAL

        # Duplicate handling is first: replay/conflict are deterministically declined.
        if idem_status == IdemStatus.DUP_REPLAY:
            return [self._decision(msg, accepted=False, reason=ReasonCode.ID_DUPLICATE_REPLAY)]
        if idem_status == IdemStatus.DUP_CONFLICT:
            return [self._decision(msg, accepted=False, reason=ReasonCode.ID_DUPLICATE_CONFLICT)]

        # Canonical path: apply policy order (attempts -> prime gate -> daily -> weekly).
        if is_canonical:
            attempt_no = snapshot.day_attempts_before + 1
            if attempt_no > self.daily_attempt_limit:
                return [self._decision(msg, accepted=False, reason=ReasonCode.DAILY_ATTEMPT_LIMIT)]

            if self.prime_enabled and msg.features.is_prime_id:
                if msg.features.effective_amount.amount > self.prime_amount_cap:
                    return [self._decision(msg, accepted=False, reason=ReasonCode.PRIME_AMOUNT_CAP)]
                if snapshot.prime_approved_count_before >= self.prime_global_per_day:
                    return [self._decision(msg, accepted=False, reason=ReasonCode.PRIME_DAILY_GLOBAL_LIMIT)]

            projected_day = snapshot.day_accepted_amount_before.amount + msg.features.effective_amount.amount
            if projected_day > self.daily_amount_limit:
                return [self._decision(msg, accepted=False, reason=ReasonCode.DAILY_AMOUNT_LIMIT)]

            projected_week = snapshot.week_accepted_amount_before.amount + msg.features.effective_amount.amount
            if projected_week > self.weekly_amount_limit:
                return [self._decision(msg, accepted=False, reason=ReasonCode.WEEKLY_AMOUNT_LIMIT)]

        return [self._decision(msg, accepted=True, reason=None)]

    def _decision(self, msg: EnrichedAttempt, *, accepted: bool, reason: ReasonCode | None) -> Decision:
        # Decision carries keys and effective_amount for UpdateWindows (Step 06).
        reasons = () if reason is None else (reason.value,)
        return Decision(
            line_no=msg.base.base.attempt.line_no,
            id=msg.base.base.attempt.id,
            customer_id=msg.base.base.attempt.customer_id,
            accepted=accepted,
            reasons=reasons,
            day_key=msg.base.base.day_key,
            week_key=msg.base.base.week_key.week_start_date,
            effective_amount=msg.features.effective_amount,
            idem_status=msg.base.idem_status,
            is_prime_id=msg.features.is_prime_id,
            is_canonical=msg.base.idem_status == IdemStatus.CANONICAL,
        )
