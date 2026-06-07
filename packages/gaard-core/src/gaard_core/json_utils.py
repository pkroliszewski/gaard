import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, Decimal):
        return _decimal_to_jsonable(value)

    if isinstance(value, datetime | date | time):
        return value.isoformat()

    if isinstance(value, bytes | bytearray | memoryview):
        return _bytes_to_jsonable(value)

    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(item) for item in value]

    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())

    return str(value)


def json_dumps(value: Any, **kwargs: Any) -> str:
    return json.dumps(to_jsonable(value), **kwargs)


def _decimal_to_jsonable(value: Decimal) -> int | float | str:
    if not value.is_finite():
        return str(value)

    if value == value.to_integral_value():
        return int(value)

    as_float = float(value)
    if math.isfinite(as_float):
        return as_float

    return str(value)


def _bytes_to_jsonable(value: bytes | bytearray | memoryview) -> str:
    raw = bytes(value)

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.hex()
