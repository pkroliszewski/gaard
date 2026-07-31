from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from gaard_connectors.odbc.exceptions import OdbcConnectorError, OdbcErrorCode

MANAGED_ODBC_KEYS = frozenset({"DSN", "DRIVER", "SERVER", "PORT", "DATABASE", "UID", "PWD"})
KEY_ORDER = ("DRIVER", "DSN", "SERVER", "PORT", "DATABASE", "UID", "PWD")


class OdbcConnectionStringBuilder:
    """Build deterministic ODBC connection strings without shell interpretation."""

    def build(self, config: Any) -> str:
        raw_connection_string = str(getattr(config, "raw_odbc_connection_string", "") or "")
        if raw_connection_string.strip():
            return raw_connection_string.strip()

        connection_mode = str(getattr(config, "connection_mode", "") or "").strip().lower()
        pairs: OrderedDict[str, str] = OrderedDict()

        if connection_mode == "dsn":
            self._add(pairs, "DSN", self._required(config, "dsn", "ODBC DSN is required."))
        elif connection_mode == "dsnless":
            driver = self._required(config, "odbc_driver", "ODBC driver is required.")
            self._add(pairs, "DRIVER", driver)

            host = str(getattr(config, "host", "") or "").strip()
            port = getattr(config, "port", None)
            if host:
                server = f"{host},{int(port)}" if port not in (None, "") else host
                self._add(pairs, "SERVER", server)

            database = str(getattr(config, "database", "") or "").strip()
            if database:
                self._add(pairs, "DATABASE", database)
        else:
            raise OdbcConnectorError(
                "ODBC connection mode must be either 'dsn' or 'dsnless'.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )

        username = str(getattr(config, "username", "") or "").strip()
        if username:
            self._add(pairs, "UID", username)

        password = getattr(config, "password", None)
        if password not in (None, ""):
            self._add(pairs, "PWD", str(password))

        extras = getattr(config, "extra_odbc_options", {}) or {}
        self._add_extra_options(pairs, extras)

        return "".join(
            f"{key}={self._format_value(key, value)};"
            for key, value in self._ordered_items(pairs)
        )

    def _required(self, config: Any, field: str, message: str) -> str:
        value = str(getattr(config, field, "") or "").strip()
        if not value:
            raise OdbcConnectorError(message, code=OdbcErrorCode.CONFIGURATION_INVALID)
        return value

    def _add(self, pairs: OrderedDict[str, str], key: str, value: str) -> None:
        normalized_key = self._normalize_key(key)
        folded = normalized_key.casefold()
        if any(existing.casefold() == folded for existing in pairs):
            raise OdbcConnectorError(
                f"Duplicate ODBC option '{normalized_key}' is not allowed.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )
        pairs[normalized_key] = str(value)

    def _add_extra_options(self, pairs: OrderedDict[str, str], extras: Mapping[str, Any]) -> None:
        seen: set[str] = set()
        for key, value in sorted(extras.items(), key=lambda item: str(item[0]).strip().casefold()):
            normalized_key = self._normalize_key(str(key))
            folded = normalized_key.casefold()
            if folded in seen:
                raise OdbcConnectorError(
                    f"Duplicate ODBC option '{normalized_key}' is not allowed.",
                    code=OdbcErrorCode.CONFIGURATION_INVALID,
                )
            seen.add(folded)

            if folded in {item.casefold() for item in MANAGED_ODBC_KEYS}:
                raise OdbcConnectorError(
                    f"ODBC option '{normalized_key}' is managed by GAARD and cannot be overridden.",
                    code=OdbcErrorCode.CONFIGURATION_INVALID,
                )
            self._add(pairs, normalized_key, "" if value is None else str(value))

    def _normalize_key(self, key: str) -> str:
        normalized = key.strip()
        if not normalized:
            raise OdbcConnectorError(
                "ODBC option names must not be empty.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )
        if ";" in normalized or "=" in normalized:
            raise OdbcConnectorError(
                f"ODBC option name '{normalized}' contains an invalid character.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )
        return normalized

    def _ordered_items(self, pairs: OrderedDict[str, str]) -> list[tuple[str, str]]:
        priority = {key: index for index, key in enumerate(KEY_ORDER)}
        return sorted(
            pairs.items(),
            key=lambda item: (
                priority.get(item[0].upper(), len(priority)),
                item[0].casefold(),
            ),
        )

    def _format_value(self, key: str, value: str) -> str:
        if key.upper() == "DRIVER":
            return self._brace_value(value)
        if self._needs_braces(value):
            return self._brace_value(value)
        return value

    def _needs_braces(self, value: str) -> bool:
        return value != value.strip() or any(char in value for char in ";{}")

    def _brace_value(self, value: str) -> str:
        normalized = value
        if normalized.startswith("{") and normalized.endswith("}"):
            normalized = normalized[1:-1]
        return "{" + normalized.replace("}", "}}") + "}"


def parse_odbc_connection_string(value: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    key_buffer: list[str] = []
    value_buffer: list[str] = []
    reading_key = True
    in_braces = False
    index = 0

    def flush() -> None:
        nonlocal key_buffer, value_buffer, reading_key
        key = "".join(key_buffer).strip()
        raw_value = "".join(value_buffer)
        if key:
            items.append((key, raw_value))
        key_buffer = []
        value_buffer = []
        reading_key = True

    while index < len(value):
        char = value[index]

        if reading_key:
            if char == "=":
                reading_key = False
            elif char == ";":
                flush()
            else:
                key_buffer.append(char)
            index += 1
            continue

        if in_braces:
            if char == "}":
                if index + 1 < len(value) and value[index + 1] == "}":
                    value_buffer.append("}")
                    index += 2
                    continue
                in_braces = False
            else:
                value_buffer.append(char)
            index += 1
            continue

        if char == "{" and not value_buffer:
            in_braces = True
        elif char == ";":
            flush()
        else:
            value_buffer.append(char)
        index += 1

    flush()
    return items
