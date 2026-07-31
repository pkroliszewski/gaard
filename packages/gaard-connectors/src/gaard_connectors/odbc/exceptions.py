from __future__ import annotations

from enum import StrEnum
from typing import Any


class OdbcErrorCode(StrEnum):
    DEPENDENCY_MISSING = "ODBC_DEPENDENCY_MISSING"
    DRIVER_MANAGER_UNAVAILABLE = "ODBC_DRIVER_MANAGER_UNAVAILABLE"
    DRIVER_NOT_FOUND = "ODBC_DRIVER_NOT_FOUND"
    DSN_NOT_FOUND = "ODBC_DSN_NOT_FOUND"
    DIALECT_NOT_FOUND = "ODBC_DIALECT_NOT_FOUND"
    CONNECTION_FAILED = "ODBC_CONNECTION_FAILED"
    AUTHENTICATION_FAILED = "ODBC_AUTHENTICATION_FAILED"
    TIMEOUT = "ODBC_TIMEOUT"
    TLS_ERROR = "ODBC_TLS_ERROR"
    SCHEMA_INSPECTION_FAILED = "ODBC_SCHEMA_INSPECTION_FAILED"
    CONFIGURATION_INVALID = "ODBC_CONFIGURATION_INVALID"
    QUERY_FAILED = "ODBC_QUERY_FAILED"


class OdbcConnectorError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: OdbcErrorCode = OdbcErrorCode.CONFIGURATION_INVALID,
        diagnostic_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic_details = diagnostic_details or {}
