from dataclasses import dataclass

from gaard_plugin_api import (
    EXTENSION_API_VERSION,
    ExtensionContext,
    ExtensionManager,
    ExtensionManifest,
    ExtensionStatus,
    discover_extensions,
)


@dataclass
class FakeEntryPoint:
    name: str
    value: object

    def load(self) -> object:
        return self.value


def test_discovery_validates_manifests_and_package_compatibility() -> None:
    manifest = ExtensionManifest(
        id="acme-warehouse",
        version="1.2.3",
        requires={"gaard-connectors": ">=0.2,<0.3"},
    )

    records = discover_extensions(
        [FakeEntryPoint("acme", manifest)],
        package_version=lambda package_name: "0.2.0",
    )

    assert len(records) == 1
    assert records[0].status == ExtensionStatus.VALIDATED
    assert records[0].manifest == manifest


def test_discovery_reports_incompatible_extensions_without_raising() -> None:
    manifest = ExtensionManifest(
        id="future-extension",
        version="1.0.0",
        extension_api_version="999",
    )

    records = discover_extensions([FakeEntryPoint("future", manifest)])

    assert records[0].status == ExtensionStatus.FAILED
    assert records[0].error is not None
    assert "requires extension API" in records[0].error


def test_manager_activates_only_declared_capability() -> None:
    received_contexts: list[ExtensionContext] = []

    def register(context: ExtensionContext) -> None:
        received_contexts.append(context)

    manifest = ExtensionManifest(
        id="acme-connector",
        version="1.0.0",
        extension_api_version=EXTENSION_API_VERSION,
        contributions={"connectors": "acme.module:register"},
    )
    manager = ExtensionManager(
        entry_point_items=[FakeEntryPoint("acme", manifest)],
        contribution_loader=lambda target: register,
    )
    registry = object()

    activated = manager.activate("connectors", registry, services={"mode": "test"})

    assert [record.manifest.id for record in activated if record.manifest] == ["acme-connector"]
    assert received_contexts[0].registry is registry
    assert received_contexts[0].services == {"mode": "test"}
    assert manager.records[0].status == ExtensionStatus.ACTIVE
    assert manager.records[0].active_capabilities == {"connectors"}

    assert manager.activate("llm", registry) == []
