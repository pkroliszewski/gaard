# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using natural language while keeping SQL generation, validation, execution, prompts, connectors, and auditability under control.

For more informacion see https://github.com/pkroliszewski/gaard

# This package
Package gaard-connectors extends gaard functionality by adding support for various database connection schemes.

Built-in SQLAlchemy datasource types include SQLite, PostgreSQL, MySQL, Oracle Database,
Microsoft SQL Server, IBM Db2, and Teradata. Install the matching optional dependency
extra for drivers that are not part of the default package.
