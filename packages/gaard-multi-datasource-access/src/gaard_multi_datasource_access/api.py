from pathlib import Path

from gaard_api.api_registry import ApiRegistry
from gaard_plugin_api import ExtensionContext


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ApiRegistry):
        raise TypeError("Multi datasource access admin section requires an ApiRegistry.")

    context.registry.register_admin_page(
        extension_id=context.extension_id,
        section_key="datasource-access",
        html_path=Path(__file__).parent / "admin" / "index.html",
        label="Multi Datasource Access",
        order=750,
        description=(
            "Enables selecting multiple active datasources and routing natural-language "
            "queries across their schemas."
        ),
    )
