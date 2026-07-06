from pathlib import Path

import pytest
from openpyxl import Workbook

from gaard_duckdb_excel_connector.connector import (
    CONNECTOR_TYPE_KEY,
    build_duckdb_excel_url,
    create_connector_definition,
    parse_duckdb_excel_url,
)


def create_workbook(path: Path) -> None:
    workbook = Workbook()
    sales = workbook.active
    sales.title = "Sales"
    sales.append(["region", "amount"])
    sales.append(["North", 10])
    customers = workbook.create_sheet("Customers")
    customers.append(["id", "name"])
    customers.append([1, "Ada"])
    workbook.save(path)


def test_definition_registers_duckdb_dialect() -> None:
    definition = create_connector_definition()

    assert definition.type_key == CONNECTOR_TYPE_KEY
    assert definition.label == "Excel File Loader"
    assert definition.default_sql_dialect == "duckdb"
    definition.validate_database_url("duckdb-excel:///tmp/catalog.duckdb?workbook=/tmp/a.xlsx&sheet=A:a")
    definition.validate_sql_dialect("duckdb")


def test_parse_url_validates_workbook_and_sheets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "sources"
    catalog_root = tmp_path / "catalogs"
    source_root.mkdir()
    catalog_root.mkdir()
    workbook_path = source_root / "sales.xlsx"
    create_workbook(workbook_path)
    monkeypatch.setenv("GAARD_DUCKDB_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("GAARD_DUCKDB_CATALOG_ROOT", str(catalog_root))

    database_url = build_duckdb_excel_url(
        catalog_root / "sales.duckdb",
        workbook_path,
        [("Sales", "sales"), ("Customers", "customers")],
    )

    config = parse_duckdb_excel_url(database_url)

    assert config.catalog_path == (catalog_root / "sales.duckdb").resolve()
    assert config.workbook_path == workbook_path.resolve()
    assert [sheet.relation_name for sheet in config.sheets] == ["sales", "customers"]


def test_parse_short_workbook_url_auto_maps_sheets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "sources"
    catalog_root = tmp_path / "catalogs"
    source_root.mkdir()
    catalog_root.mkdir()
    workbook_path = source_root / "sales.xlsx"
    create_workbook(workbook_path)
    monkeypatch.setenv("GAARD_DUCKDB_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("GAARD_DUCKDB_CATALOG_ROOT", str(catalog_root))

    config = parse_duckdb_excel_url(f"duckdb-excel:///{workbook_path.as_posix()}")

    assert config.catalog_path == (catalog_root / "sales.duckdb").resolve()
    assert config.workbook_path == workbook_path.resolve()
    assert [(sheet.sheet_name, sheet.relation_name) for sheet in config.sheets] == [
        ("Sales", "sales"),
        ("Customers", "customers"),
    ]


def test_parse_short_url_accepts_escaped_unix_spaces(tmp_path: Path) -> None:
    workbook_path = tmp_path / "sales file.xlsx"
    create_workbook(workbook_path)

    config = parse_duckdb_excel_url(
        f"duckdb-excel:///{str(workbook_path).replace(' ', r'\\ ')}?sample_size=25"
    )

    assert config.workbook_path == workbook_path.resolve()
    assert config.sheets[0].type_sample_rows == 25


def test_parse_url_rejects_duplicate_relations(tmp_path: Path) -> None:
    workbook_path = tmp_path / "sales.xlsx"
    create_workbook(workbook_path)
    database_url = build_duckdb_excel_url(
        tmp_path / "sales.duckdb",
        workbook_path,
        [("Sales", "sales"), ("Customers", "sales")],
    )

    with pytest.raises(ValueError, match="unique"):
        parse_duckdb_excel_url(database_url)


def test_parse_url_rejects_missing_sheet(tmp_path: Path) -> None:
    workbook_path = tmp_path / "sales.xlsx"
    create_workbook(workbook_path)
    database_url = build_duckdb_excel_url(
        tmp_path / "sales.duckdb",
        workbook_path,
        [("Missing", "missing")],
    )

    with pytest.raises(ValueError, match="missing sheet"):
        parse_duckdb_excel_url(database_url)


def test_parse_url_rejects_workbook_outside_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    workbook_path = tmp_path / "sales.xlsx"
    create_workbook(workbook_path)
    monkeypatch.setenv("GAARD_DUCKDB_SOURCE_ROOT", str(source_root))

    database_url = build_duckdb_excel_url(
        tmp_path / "sales.duckdb",
        workbook_path,
        [("Sales", "sales")],
    )

    with pytest.raises(ValueError, match="GAARD_DUCKDB_SOURCE_ROOT"):
        parse_duckdb_excel_url(database_url)


def test_parse_url_accepts_windows_drive_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    normalize_url_path = parse_duckdb_excel_url.__globals__["_normalize_url_path"]

    assert normalize_url_path("/C:/Users/itechnologie/GAARD/dane.duckdb") == (
        "C:/Users/itechnologie/GAARD/dane.duckdb"
    )
    assert normalize_url_path(r"/C:\Users\itechnologie\GAARD\dane.duckdb") == (
        r"C:\Users\itechnologie\GAARD\dane.duckdb"
    )
    assert normalize_url_path(r"/C:\Users\itechnologie\GAARD\sales\\ file.xlsx") == (
        r"C:\Users\itechnologie\GAARD\sales file.xlsx"
    )
