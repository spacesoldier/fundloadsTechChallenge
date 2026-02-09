from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from fund_load.domain.money import Money
from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore


class WindowStateKVStore(KVStore):
    # Marker KV contract for window-state storage.
    pass


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    # Immutable read model used by policy checks.
    day_attempts_before: int
    day_accepted_amount_before: Money
    week_accepted_amount_before: Money
    prime_approved_count_before: int


@runtime_checkable
class WindowStoreService(Protocol):
    # Service API for read/update operations over window state.
    def read_snapshot(self, *, customer_id: str, day_key: date, week_key: date) -> WindowSnapshot:
        ...

    def inc_daily_attempts(self, *, customer_id: str, day_key: date, delta: int = 1) -> None:
        ...

    def add_daily_accepted_amount(self, *, customer_id: str, day_key: date, amount: Money) -> None:
        ...

    def add_weekly_accepted_amount(self, *, customer_id: str, week_key: date, amount: Money) -> None:
        ...

    def inc_daily_prime_gate(self, *, day_key: date, delta: int = 1) -> None:
        ...


@service(name="window_store_service")
@dataclass
class InMemoryWindowStore(WindowStoreService):
    # Service reads/writes through framework KV contract.
    store: WindowStateKVStore = inject.kv(WindowStateKVStore, qualifier="window_state")

    @staticmethod
    def _key_daily_attempts(customer_id: str, day_key: date) -> str:
        return f"daily_attempts:{customer_id}:{day_key.isoformat()}"

    @staticmethod
    def _key_daily_amount(customer_id: str, day_key: date) -> str:
        return f"daily_accepted_amount:{customer_id}:{day_key.isoformat()}"

    @staticmethod
    def _key_weekly_amount(customer_id: str, week_key: date) -> str:
        return f"weekly_accepted_amount:{customer_id}:{week_key.isoformat()}"

    @staticmethod
    def _key_prime_gate(day_key: date) -> str:
        return f"prime_daily_gate:{day_key.isoformat()}"

    def _get_int(self, key: str) -> int:
        value = self.store.get(key)
        if isinstance(value, int):
            return value
        return 0

    def _add_int(self, key: str, delta: int) -> None:
        self.store.set(key, self._get_int(key) + delta)

    def read_snapshot(self, *, customer_id: str, day_key: date, week_key: date) -> WindowSnapshot:
        day_attempts = self._get_int(self._key_daily_attempts(customer_id, day_key))
        day_amount_cents = self._get_int(self._key_daily_amount(customer_id, day_key))
        week_amount_cents = self._get_int(self._key_weekly_amount(customer_id, week_key))
        prime_used = self._get_int(self._key_prime_gate(day_key))
        return WindowSnapshot(
            day_attempts_before=day_attempts,
            day_accepted_amount_before=_money_from_cents(day_amount_cents),
            week_accepted_amount_before=_money_from_cents(week_amount_cents),
            prime_approved_count_before=prime_used,
        )

    def inc_daily_attempts(self, *, customer_id: str, day_key: date, delta: int = 1) -> None:
        self._add_int(self._key_daily_attempts(customer_id, day_key), delta)

    def add_daily_accepted_amount(self, *, customer_id: str, day_key: date, amount: Money) -> None:
        self._add_int(self._key_daily_amount(customer_id, day_key), _to_cents(amount))

    def add_weekly_accepted_amount(self, *, customer_id: str, week_key: date, amount: Money) -> None:
        self._add_int(self._key_weekly_amount(customer_id, week_key), _to_cents(amount))

    def inc_daily_prime_gate(self, *, day_key: date, delta: int = 1) -> None:
        self._add_int(self._key_prime_gate(day_key), delta)


@adapter(
    name="window_store",
    kind="memory.window_store",
    consumes=[],
    emits=[],
    binds=[("service", WindowStoreService)],
)
def window_store_memory(settings: dict[str, Any]) -> InMemoryWindowStore:
    _ = settings
    return InMemoryWindowStore()


def _to_cents(amount: Money) -> int:
    cents = (amount.amount.quantize(Decimal("0.01")) * 100).to_integral_value()
    return int(cents)


def _money_from_cents(cents: int) -> Money:
    value = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
    return Money(currency="USD", amount=value)
