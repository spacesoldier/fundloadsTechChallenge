from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.debug_payload import (
    build_call_payload_fields,
    build_result_payload_fields,
    payload_model_name,
    serialize_debug_value,
)


@dataclass
class _Data:
    x: int
    y: str


def test_payload_model_name_for_builtin() -> None:
    assert payload_model_name({"a": 1}) == "dict"


def test_serialize_debug_value_for_dataclass() -> None:
    value = serialize_debug_value(_Data(x=7, y="q"))
    assert isinstance(value, dict)
    assert value.get("x") == 7
    assert value.get("y") == "q"


def test_build_call_payload_fields_extracts_payload_argument() -> None:
    fields = build_call_payload_fields(
        method="send",
        args=("worker", {"k": "v"}),
        kwargs={},
    )
    assert fields.get("payload_model") == "dict"
    assert fields.get("payload") == {"k": "v"}
    assert fields.get("args_values") == ["worker", {"k": "v"}]


def test_build_result_payload_fields_serializes_result_value() -> None:
    fields = build_result_payload_fields(result={"ok": True, "value": 7})
    assert fields.get("result_model") == "dict"
    assert fields.get("result") == {"ok": True, "value": 7}
