from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

EXTENSION_API_VERSION = "1"
_EXTENSION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class ExtensionManifestError(ValueError):
    """Raised when an extension manifest does not satisfy the public contract."""


class ExtensionCompatibilityError(RuntimeError):
    """Raised when an installed extension is incompatible with the host."""


class ExtensionActivationError(RuntimeError):
    """Raised when an extension contribution cannot be activated."""


class ExtensionStatus(StrEnum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    """Public metadata describing one installed GAARD extension."""

    id: str
    version: str
    extension_api_version: str = EXTENSION_API_VERSION
    requires: Mapping[str, str] = field(default_factory=dict)
    contributions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _EXTENSION_ID_PATTERN.fullmatch(self.id):
            raise ExtensionManifestError(
                "Extension id must use lowercase letters, digits, and hyphens, "
                "and must start with a letter."
            )

        if not self.version.strip():
            raise ExtensionManifestError("Extension version must not be empty.")

        for package_name, version_specifier in self.requires.items():
            if not package_name.strip() or not version_specifier.strip():
                raise ExtensionManifestError(
                    "Extension package requirements must contain a package name and version specifier."
                )

        for capability, target in self.contributions.items():
            if not capability.strip():
                raise ExtensionManifestError("Extension contribution capability must not be empty.")
            if not _is_import_target(target):
                raise ExtensionManifestError(
                    "Extension contribution targets must use the 'module:attribute' format."
                )

        object.__setattr__(self, "requires", MappingProxyType(dict(self.requires)))
        object.__setattr__(self, "contributions", MappingProxyType(dict(self.contributions)))


@dataclass(slots=True)
class ExtensionRecord:
    """Runtime state for a discovered extension."""

    entry_point_name: str
    manifest: ExtensionManifest | None = None
    status: ExtensionStatus = ExtensionStatus.DISCOVERED
    error: str | None = None
    active_capabilities: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    """Controlled context supplied to one extension contribution factory."""

    extension_id: str
    capability: str
    registry: Any
    services: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))


def _is_import_target(value: str) -> bool:
    module_name, separator, attribute_name = value.partition(":")
    return bool(separator and module_name.strip() and attribute_name.strip())
