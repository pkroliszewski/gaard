# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using natural language while keeping SQL generation, validation, execution, prompts, connectors, and auditability under control.

For more informacion see https://github.com/pkroliszewski/gaard

# This package
Package gaard-connectors extends gaard functionality by adding support for various database connection schemes.

Built-in SQLAlchemy datasource types include SQLite, PostgreSQL, MySQL, Oracle Database,
Microsoft SQL Server, IBM Db2, Teradata, and ODBC / unixODBC. Install the matching
optional dependency extra for drivers that are not part of the default package.

## ODBC / unixODBC

The `odbc` datasource type connects through the existing GAARD SQLAlchemy pipeline:

```text
GAARD -> SQLAlchemy dialect -> pyodbc -> unixODBC -> vendor ODBC driver -> database
```

ODBC is not a SQL dialect. Configure the real SQLAlchemy driver name, for example
`mssql+pyodbc`, and the unixODBC driver or DSN separately.

Python dependency:

```bash
python -m pip install 'gaard-connectors[odbc]'
```

System dependency on Oracle Linux 8/9:

```bash
dnf install -y unixODBC unixODBC-devel
```

Vendor dependency: install the ODBC driver for the selected database according to
the vendor license and instructions.

Useful diagnostics:

```bash
odbcinst -j
odbcinst -q -d
python -c "import pyodbc; print(pyodbc.drivers())"
```

DSN mode uses a DSN already configured in `odbc.ini`:

```text
connection_mode=dsn
sqlalchemy_drivername=mssql+pyodbc
dsn=hospital_reporting
username=gaard_reader
password=...
```

DSN-less mode sends the connection attributes directly through `odbc_connect`:

```text
connection_mode=dsnless
sqlalchemy_drivername=mssql+pyodbc
odbc_driver=ODBC Driver 18 for SQL Server
host=sql01.internal
port=1433
database=ERP
username=gaard_reader
password=...
Encrypt=yes
TrustServerCertificate=yes
```

For Docker or Podman, unixODBC, `odbc.ini`, `odbcinst.ini`, and the vendor driver
library must be available inside the GAARD API container. Mounting only `.ini`
files is not enough if the referenced shared library is missing.

When the ODBC setup lives on a different machine than GAARD, prefer DSN-less
configuration against the remote database host or run the GAARD API/worker beside
that unixODBC installation. A DSN name on a remote Unix host is not automatically
visible to a local GAARD process because unixODBC is loaded in-process by pyodbc.
