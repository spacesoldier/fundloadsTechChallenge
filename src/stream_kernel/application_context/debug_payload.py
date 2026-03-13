from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

_PAYLOAD_HINT_KEYS = (
    "payload",
    "message",
    "event",
    "data",
    "item",
    "record",
    "envelope",
    "body",
    "value",
)


def build_call_payload_fields(
    *,
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> dict[str, object]:
    payload = _select_payload(method=method, args=args, kwargs=kwargs)
    result: dict[str, object] = {
        "args_values": serialize_debug_value(list(args)),
        "kwargs_values": serialize_debug_value(dict(kwargs)),
    }
    if payload is not None:
        result["payload_model"] = payload_model_name(payload)
        result["payload"] = serialize_debug_value(payload)
    return result


def build_result_payload_fields(*, result: object) -> dict[str, object]:
    if result is None:
        return {}
    return {
        "result_model": payload_model_name(result),
        "result": serialize_debug_value(result),
    }


def payload_model_name(payload: object) -> str:
    payload_type = type(payload)
    module = payload_type.__module__
    name = payload_type.__name__
    if isinstance(module, str) and module and module != "builtins":
        return f"{module}.{name}"
    return name


def serialize_debug_value(value: object) -> object:
    return _serialize(value=value, visited=set(), depth=0, max_depth=16)


def _serialize(*, value: object, visited: set[int], depth: int, max_depth: int) -> object:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.hex()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        enum_value = value.value
        if isinstance(enum_value, (str, int, float, bool)):
            return enum_value
        return value.name

    if depth >= max_depth:
        return repr(value)

    if isinstance(value, Mapping):
        obj_id = id(value)
        if obj_id in visited:
            return {"__cycle__": type(value).__name__}
        visited.add(obj_id)
        serialized: dict[str, object] = {}
        for key, item in value.items():
            serialized[str(key)] = _serialize(
                value=item,
                visited=visited,
                depth=depth + 1,
                max_depth=max_depth,
            )
        return serialized

    if _is_sequence(value):
        obj_id = id(value)
        if obj_id in visited:
            return [f"<cycle:{type(value).__name__}>"]
        visited.add(obj_id)
        return [
            _serialize(value=item, visited=visited, depth=depth + 1, max_depth=max_depth)
            for item in value
        ]

    if is_dataclass(value):
        obj_id = id(value)
        if obj_id in visited:
            return {"__cycle__": type(value).__name__}
        visited.add(obj_id)
        return {
            "__type__": payload_model_name(value),
            **{
                item.name: _serialize(
                    value=getattr(value, item.name),
                    visited=visited,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                for item in dataclass_fields(value)
            },
        }

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        except Exception:
            dumped = None
        if dumped is not None:
            return {
                "__type__": payload_model_name(value),
                "data": _serialize(value=dumped, visited=visited, depth=depth + 1, max_depth=max_depth),
            }

    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        obj_id = id(value)
        if obj_id in visited:
            return {"__cycle__": type(value).__name__}
        visited.add(obj_id)
        serialized_dict: dict[str, object] = {"__type__": payload_model_name(value)}
        for key, item in value_dict.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            serialized_dict[str(key)] = _serialize(
                value=item,
                visited=visited,
                depth=depth + 1,
                max_depth=max_depth,
            )
        return serialized_dict

    return repr(value)


def _is_sequence(value: object) -> bool:
    if isinstance(value, (str, bytes, bytearray)):
        return False
    return isinstance(value, Sequence) or isinstance(value, set)


def _select_payload(
    *,
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object | None:
    for key in _PAYLOAD_HINT_KEYS:
        if key in kwargs:
            return kwargs[key]
    lowered_method = method.lower()
    if len(args) >= 2 and lowered_method in {
        "send",
        "publish",
        "emit",
        "dispatch",
        "route",
        "write",
        "push",
    }:
        first = args[0]
        if isinstance(first, str):
            return args[1]
    if args:
        return args[0]
    return None


__all__ = [
    "build_call_payload_fields",
    "build_result_payload_fields",
    "payload_model_name",
    "serialize_debug_value",
]
