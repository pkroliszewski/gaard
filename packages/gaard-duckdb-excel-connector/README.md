# GAARD Excel File Loader

Private GAARD extension package that registers an installable Excel workbook datasource loader.

The connector lets GAARD query configured `.xlsx` sheets as normal DuckDB relations. GAARD SQL should reference relation names only:

```sql
SELECT region, SUM(amount) AS revenue
FROM sales
GROUP BY region
ORDER BY revenue DESC
```

## Install

From a GAARD development checkout:

```bash
python -m pip install -e private/packages/gaard-duckdb-excel-connector
```

If an older version was installed before, upgrade dependencies too:

```bash
python -m pip install -U -e private/packages/gaard-duckdb-excel-connector
```

`read_xlsx` requires the DuckDB Excel extension available in DuckDB 1.4.x.

## Connector URL

The current GAARD extension API passes only `database_url` into connector factories, so this package carries the Excel adapter configuration in the private connector URL.

Shortest form:

```text
duckdb-excel:///absolute/path/sales.xlsx
```

In the short form:

- all workbook sheets are mapped automatically;
- relation names are generated from sheet names, for example `Sales 2026` becomes `sales_2026`;
- the DuckDB catalog is created next to the workbook as `sales.duckdb`;
- if `GAARD_DUCKDB_CATALOG_ROOT` is set, the catalog is created there instead.

Explicit form:

```text
duckdb-excel:///absolute/path/catalog.duckdb?workbook=/absolute/path/sales.xlsx&sheet=Sales:sales&sheet=Customers:customers&mode=table
```

Parameters:

- `workbook`: absolute path to an existing `.xlsx` file.
- `sheet`: repeatable `Sheet Name:relation_name` mapping.
- `mode`: `table` for materialized tables or `view` to always read the current workbook.
- `type_sample_rows`: optional loader-side number of data rows used to infer a whole-column type.
- `header`: optional, defaults to `true`.
- `all_varchar`: optional, defaults to `false`.
- `ignore_errors`: optional, defaults to `false`.
- `range`: optional Excel range applied to every configured sheet.

By default DuckDB's native Excel reader infers column types from the first data
row. When `type_sample_rows` is set, the loader reads that many data rows with
`openpyxl`, infers a column type from the observed values, reads the workbook as
text, and casts the generated relation columns to the inferred types.

Security roots:

- `GAARD_DUCKDB_SOURCE_ROOT`: allowed root for workbooks.
- `GAARD_DUCKDB_CATALOG_ROOT`: allowed root for DuckDB catalog files.

When a root is set, paths are resolved and must remain inside it.

## Example

```text
duckdb-excel:///srv/gaard/duckdb/sales.duckdb?workbook=/srv/gaard/sources/sales.xlsx&sheet=Sales:sales&sheet=Customers:customers&mode=table
```

The connector registers as `duckdb-excel`, appears in the UI as `Excel File Loader`, and uses SQL dialect `duckdb`.

The package also registers a small `Extensions -> Excel File Loader` admin section so
that the installed extension is visible in the admin extension menu. The actual
datasource is still configured from the Datasources screen.
