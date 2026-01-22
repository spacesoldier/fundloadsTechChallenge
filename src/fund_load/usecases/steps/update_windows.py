from __future__ import annotations

from dataclasses import dataclass

from fund_load.ports.window_store import WindowWritePort
from fund_load.usecases.messages import Decision


@dataclass(frozen=True, slots=True)
class UpdateWindows:
    # Step 06 mutates window state based on Decision (docs/implementation/steps/06 UpdateWindows.md).
    window_store: WindowWritePort
    prime_gate_enabled: bool

    def __call__(self, msg: Decision, ctx: object | None) -> list[Decision]:
        # Non-canonical attempts must not mutate any windows (Step 06 invariant).
        if not msg.is_canonical:
            return [msg]

        # Attempts are incremented for every canonical decision (accepted or declined).
        self.window_store.inc_daily_attempts(
            customer_id=msg.customer_id,
            day_key=msg.day_key,
        )

        if msg.accepted:
            # Accepted sums update only on approval; uses effective_amount (Step 06).
            self.window_store.add_daily_accepted_amount(
                customer_id=msg.customer_id,
                day_key=msg.day_key,
                amount=msg.effective_amount,
            )
            self.window_store.add_weekly_accepted_amount(
                customer_id=msg.customer_id,
                week_key=msg.week_key,
                amount=msg.effective_amount,
            )

            # Prime gate is global per day and increments only for approved prime attempts.
            if self.prime_gate_enabled and msg.is_prime_id:
                self.window_store.inc_daily_prime_gate(day_key=msg.day_key)

        return [msg]
