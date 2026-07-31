from __future__ import annotations

from gaard_connectors.odbc.connection_string import parse_odbc_connection_string

SECRET_ODBC_KEYS = frozenset(
    {
        "PWD",
        "PASSWORD",
        "TOKEN",
        "ACCESS_TOKEN",
        "API_KEY",
        "SECRET",
    }
)


def redact_odbc_connection_string(value: str) -> str:
    if not value:
        return value

    redacted: list[str] = []
    for key, option_value in parse_odbc_connection_string(value):
        safe_value = "***" if key.strip().upper() in SECRET_ODBC_KEYS else option_value
        redacted.append(f"{key.strip()}={_format_odbc_value(key, safe_value)};")
    return "".join(redacted)


def odbc_connection_string_has_secret(value: str) -> bool:
    return any(
        key.strip().upper() in SECRET_ODBC_KEYS and option_value != ""
        for key, option_value in parse_odbc_connection_string(value)
    )


def _format_odbc_value(key: str, value: str) -> str:
    if key.strip().upper() == "DRIVER" or value != value.strip() or any(
        char in value for char in ";{}"
    ):
        normalized = value[1:-1] if value.startswith("{") and value.endswith("}") else value
        return "{" + normalized.replace("}", "}}") + "}"
    return value
