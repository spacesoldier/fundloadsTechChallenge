from __future__ import annotations

from dataclasses import dataclass

from fund_load.ports.window_store import WindowWritePort
from fund_load.usecases.messages import Decision
from stream_kernel.application_context.config_inject import config
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node


# Discovery: register step name for pipeline assembly (docs/implementation/steps/06 UpdateWindows.md).
# consumes/emits are used for DAG construction (docs/framework/initial_stage/DAG construction.md).
@node(name="update_windows", consumes=[Decision], emits=[Decision])
@dataclass(frozen=True, slots=True)
class UpdateWindows:
    # Step 06 mutates window state based on Decision (docs/implementation/steps/06 UpdateWindows.md).
    # Dependency injection: WindowStore write port is provided by the runtime wiring.
    # We use the generic "kv" port_type as a service bucket in this initial stage.
    window_store: WindowWritePort = inject.kv(WindowWritePort)
    # Config-driven toggle comes from nodes.update_windows.daily_prime_gate.enabled (newgen config).
    prime_gate_enabled: bool = config.value("daily_prime_gate.enabled", default=False)

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
