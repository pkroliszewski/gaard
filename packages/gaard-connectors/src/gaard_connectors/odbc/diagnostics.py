from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OdbcDiagnostics:
    platform: str
    python_version: str
    sqlalchemy_version: str
    pyodbc_version: str | None
    unixodbc_available: bool | None
    odbcinst_available: bool
    isql_available: bool
    installed_drivers: list[str]
    configured_system_dsns: list[str] | None
    configured_user_dsns: list[str] | None
    environment: dict[str, str]

    def serialize(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "python_version": self.python_version,
            "sqlalchemy_version": self.sqlalchemy_version,
            "pyodbc_version": self.pyodbc_version,
            "unixodbc_available": self.unixodbc_available,
            "odbcinst_available": self.odbcinst_available,
            "isql_available": self.isql_available,
            "installed_drivers": self.installed_drivers,
            "configured_system_dsns": self.configured_system_dsns,
            "configured_user_dsns": self.configured_user_dsns,
            "environment": self.environment,
        }


def list_odbc_drivers() -> list[str]:
    pyodbc = _import_pyodbc()
    if pyodbc is None:
        return []
    return sorted(str(driver) for driver in pyodbc.drivers())


def list_configured_dsns() -> dict[str, list[str]]:
    user_dsns = _pyodbc_data_sources()
    system_dsns = _odbcinst_query("-s")
    return {
        "system": system_dsns or [],
        "user": sorted(user_dsns),
    }


def collect_diagnostics() -> OdbcDiagnostics:
    pyodbc = _import_pyodbc()
    odbcinst_available = shutil.which("odbcinst") is not None
    dsns = list_configured_dsns()
    return OdbcDiagnostics(
        platform=platform.platform(),
        python_version=sys.version.split()[0],
        sqlalchemy_version=_package_version("SQLAlchemy"),
        pyodbc_version=_package_version("pyodbc") if pyodbc is not None else None,
        unixodbc_available=True if pyodbc is not None else None,
        odbcinst_available=odbcinst_available,
        isql_available=shutil.which("isql") is not None,
        installed_drivers=list_odbc_drivers(),
        configured_system_dsns=dsns["system"],
        configured_user_dsns=dsns["user"],
        environment={
            key: value
            for key in ("ODBCINI", "ODBCSYSINI", "ODBCINSTINI")
            if (value := os.environ.get(key))
        },
    )


def _import_pyodbc() -> Any | None:
    try:
        import pyodbc  # type: ignore[import-not-found]
    except ImportError:
        return None
    return pyodbc


def _package_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _pyodbc_data_sources() -> list[str]:
    pyodbc = _import_pyodbc()
    if pyodbc is None or not hasattr(pyodbc, "dataSources"):
        return []

    data_sources = pyodbc.dataSources()
    if isinstance(data_sources, dict):
        return sorted(str(name) for name in data_sources)
    return []


def _odbcinst_query(option: str) -> list[str] | None:
    if shutil.which("odbcinst") is None:
        return None
    try:
        completed = subprocess.run(
            ["odbcinst", "-q", option],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return sorted(
        line.strip().strip("[]")
        for line in completed.stdout.splitlines()
        if line.strip()
    )
