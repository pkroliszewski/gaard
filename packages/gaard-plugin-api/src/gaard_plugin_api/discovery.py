from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from importlib import import_module
from importlib.metadata import PackageNotFoundError, entry_points, version
from typing import Any, Protocol, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from gaard_plugin_api.models import (
    EXTENSION_API_VERSION,
    ExtensionActivationError,
    ExtensionCompatibilityError,
    ExtensionContext,
    ExtensionManifest,
    ExtensionManifestError,
    ExtensionRecord,
    ExtensionStatus,
)


EXTENSION_ENTRY_POINT_GROUP = "gaard.extensions"


class EntryPointLike(Protocol):
    name: str

    def load(self) -> Any: ...


ContributionLoader = Callable[[str], Callable[[ExtensionContext], None]]
PackageVersionResolver = Callable[[str], str]


def discover_extensions(
    entry_point_items: Iterable[EntryPointLike] | None = None,
    package_version: PackageVersionResolver = version,
) -> list[ExtensionRecord]:
    """Discover installed manifests without activating their contributions."""

    selected_entry_points = (
        list(entry_point_items)
        if entry_point_items is not None
        else list(entry_points(group=EXTENSION_ENTRY_POINT_GROUP))
    )
    records: list[ExtensionRecord] = []

    for entry_point in sorted(selected_entry_points, key=lambda item: item.name):
        record = ExtensionRecord(entry_point_name=entry_point.name)
        records.append(record)

        try:
            manifest = _load_manifest(entry_point)
            _validate_compatibility(manifest, package_version)
        except (
            ExtensionCompatibilityError,
            ExtensionManifestError,
            PackageNotFoundError,
            TypeError,
            ValueError,
        ) as exc:
            record.status = ExtensionStatus.FAILED
            record.error = str(exc)
            continue
        except Exception as exc:  # pragma: no cover - defensive boundary for third-party code
            record.status = ExtensionStatus.FAILED
            record.error = f"Unable to load extension manifest: {exc}"
            continue

        record.manifest = manifest
        record.status = ExtensionStatus.VALIDATED

    return records


class ExtensionManager:
    """Discovers and activates typed extension contributions for one host process."""

    def __init__(
        self,
        entry_point_items: Iterable[EntryPointLike] | None = None,
        package_version: PackageVersionResolver = version,
        contribution_loader: ContributionLoader | None = None,
    ) -> None:
        self._entry_point_items = entry_point_items
        self._package_version = package_version
        self._contribution_loader = contribution_loader or load_contribution
        self.records: list[ExtensionRecord] = []

    def discover(self) -> list[ExtensionRecord]:
        self.records = discover_extensions(
            entry_point_items=self._entry_point_items,
            package_version=self._package_version,
        )
        return self.records

    def activate(
        self,
        capability: str,
        registry: Any,
        services: Mapping[str, Any] | None = None,
    ) -> list[ExtensionRecord]:
        """Activate one capability for every validated extension that declares it."""

        if not self.records:
            self.discover()

        activated: list[ExtensionRecord] = []

        for record in self.records:
            if record.status not in {ExtensionStatus.VALIDATED, ExtensionStatus.ACTIVE}:
                continue
            if record.manifest is None or capability not in record.manifest.contributions:
                continue
            if capability in record.active_capabilities:
                continue

            target = record.manifest.contributions[capability]
            context = ExtensionContext(
                extension_id=record.manifest.id,
                capability=capability,
                registry=registry,
                services=services or {},
            )

            try:
                contribution = self._contribution_loader(target)
                contribution(context)
            except Exception as exc:  # pragma: no cover - defensive boundary for third-party code
                record.status = ExtensionStatus.FAILED
                record.error = f"Unable to activate {capability!r} contribution: {exc}"
                continue

            record.active_capabilities.add(capability)
            record.status = ExtensionStatus.ACTIVE
            activated.append(record)

        return activated


def load_contribution(target: str) -> Callable[[ExtensionContext], None]:
    """Import one declared contribution factory from a `module:attribute` target."""

    module_name, separator, attribute_name = target.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ExtensionActivationError(
            "Extension contribution targets must use the 'module:attribute' format."
        )

    module = import_module(module_name)
    contribution = getattr(module, attribute_name)

    if not callable(contribution):
        raise ExtensionActivationError(f"Extension contribution {target!r} is not callable.")

    return cast(Callable[[ExtensionContext], None], contribution)


def _load_manifest(entry_point: EntryPointLike) -> ExtensionManifest:
    candidate = entry_point.load()
    manifest = candidate() if callable(candidate) else candidate

    if not isinstance(manifest, ExtensionManifest):
        raise ExtensionManifestError(
            f"Entry point {entry_point.name!r} must resolve to an ExtensionManifest or a factory."
        )

    return manifest


def _validate_compatibility(
    manifest: ExtensionManifest,
    package_version: PackageVersionResolver,
) -> None:
    if manifest.extension_api_version != EXTENSION_API_VERSION:
        raise ExtensionCompatibilityError(
            f"Extension {manifest.id!r} requires extension API "
            f"{manifest.extension_api_version!r}, but host supports {EXTENSION_API_VERSION!r}."
        )

    for package_name, version_specifier in manifest.requires.items():
        try:
            requirement = SpecifierSet(version_specifier)
        except InvalidSpecifier as exc:
            raise ExtensionCompatibilityError(
                f"Extension {manifest.id!r} has invalid version specifier "
                f"{version_specifier!r} for {package_name!r}."
            ) from exc

        installed_version = package_version(package_name)
        if installed_version not in requirement:
            raise ExtensionCompatibilityError(
                f"Extension {manifest.id!r} requires {package_name}{version_specifier}, "
                f"but {installed_version} is installed."
            )
