from datetime import date, datetime
from decimal import Decimal

from gaard_core.json_utils import json_dumps, to_jsonable


def test_to_jsonable_normalizes_common_database_values() -> None:
    assert to_jsonable(
        {
            "integer_decimal": Decimal("30"),
            "fractional_decimal": Decimal("30.5"),
            "event_date": date(2026, 5, 24),
            "event_time": datetime(2026, 5, 24, 9, 30),
            "payload": b"ok",
            "binary_payload": b"\xff",
        }
    ) == {
        "integer_decimal": 30,
        "fractional_decimal": 30.5,
        "event_date": "2026-05-24",
        "event_time": "2026-05-24T09:30:00",
        "payload": "ok",
        "binary_payload": "ff",
    }


def test_json_dumps_serializes_common_database_values() -> None:
    payload = {"rows": [{"total_minutes": Decimal("42")}]}

    assert '"total_minutes": 42' in json_dumps(payload)
