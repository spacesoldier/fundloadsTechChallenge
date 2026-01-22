from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from fund_load.domain.messages import Decision, LoadAttempt, RawLine
from fund_load.domain.money import MoneyParseError, parse_money
from fund_load.domain.reasons import ReasonCode


class _RawLoadAttempt(BaseModel):
    id: str
    customer_id: str
    load_amount: str
    time: str

    model_config = ConfigDict(extra="ignore")


class ParseLoadAttempt:
    def __call__(self, msg: RawLine, ctx: object | None) -> list[LoadAttempt | Decision]:
        # Step 01 requires exactly one output per input line (docs/implementation/steps/01 ParseLoadAttempt.md).
        try:
            payload = json.loads(msg.raw_text)
        except json.JSONDecodeError:
            return [self._decline(msg.line_no, "", "", ReasonCode.INPUT_PARSE_ERROR)]

        if not isinstance(payload, dict):
            return [self._decline(msg.line_no, "", "", ReasonCode.INPUT_PARSE_ERROR)]

        try:
            raw = _RawLoadAttempt.model_validate(payload)
        except ValidationError:
            # Best-effort id fields keep output deterministic even on schema failures.
            id_value, customer_value = _extract_id_fields(payload)
            return [
                self._decline(
                    msg.line_no, id_value, customer_value, ReasonCode.INPUT_PARSE_ERROR
                )
            ]

        try:
            id_value = _normalize_id(raw.id)
        except ValueError:
            # Invalid ids are declined deterministically (Reason Codes spec).
            return [
                self._decline(
                    msg.line_no,
                    str(raw.id).strip(),
                    str(raw.customer_id).strip(),
                    ReasonCode.INVALID_ID_FORMAT,
                )
            ]

        try:
            customer_value = _normalize_id(raw.customer_id)
        except ValueError:
            return [
                self._decline(
                    msg.line_no,
                    id_value,
                    str(raw.customer_id).strip(),
                    ReasonCode.INVALID_ID_FORMAT,
                )
            ]

        try:
            ts = _parse_timestamp(raw.time)
        except ValueError:
            return [
                self._decline(
                    msg.line_no, id_value, customer_value, ReasonCode.INVALID_TIMESTAMP
                )
            ]

        try:
            amount = parse_money(raw.load_amount, currency="USD")
        except MoneyParseError:
            return [
                self._decline(
                    msg.line_no,
                    id_value,
                    customer_value,
                    ReasonCode.INVALID_AMOUNT_FORMAT,
                )
            ]

        attempt = LoadAttempt(
            line_no=msg.line_no,
            id=id_value,
            customer_id=customer_value,
            amount=amount,
            ts=ts,
            raw=payload,
        )
        return [attempt]

    @staticmethod
    def _decline(
        line_no: int, id_value: str, customer_value: str, reason: ReasonCode
    ) -> Decision:
        return Decision(
            line_no=line_no,
            id=id_value,
            customer_id=customer_value,
            accepted=False,
            reasons=(reason.value,),
        )


_ID_PATTERN = re.compile(r"^\d+$")


def _normalize_id(value: str) -> str:
    # IDs must be digit-only strings per Step 01 spec.
    text = value.strip()
    if not text or not _ID_PATTERN.match(text):
        raise ValueError("id must be digits")
    return text


def _parse_timestamp(value: str) -> datetime:
    # Parse ISO8601 timestamps and normalize to UTC (Time and Money Semantics doc).
    text = value.strip()
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc

    if ts.tzinfo is None:
        raise ValueError("timestamp missing timezone")

    return ts.astimezone(UTC)


def _extract_id_fields(payload: dict[str, Any]) -> tuple[str, str]:
    # Fallback extraction keeps output stable for malformed lines.
    id_value = ""
    customer_value = ""
    if "id" in payload:
        id_value = str(payload["id"]).strip()
    if "customer_id" in payload:
        customer_value = str(payload["customer_id"]).strip()
    return id_value, customer_value
