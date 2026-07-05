from pathlib import Path

from gaard_api.api_registry import ApiRegistry
from gaard_plugin_api import ExtensionContext


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ApiRegistry):
        raise TypeError("DuckDB Excel admin page requires an ApiRegistry.")

    context.registry.register_admin_page(
        extension_id=context.extension_id,
        section_key="duckdb-excel",
        html_path=Path(__file__).parent / "admin" / "index.html",
        label="Excel File Loader",
        order=700,
        description="Excel workbook datasource loader.",
    )
