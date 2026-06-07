from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from gaard_core.schema.models import ColumnInfo, DatabaseSchema, ForeignKeyInfo, TableInfo


class SQLAlchemySchemaIntrospector:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: Engine = create_engine(database_url)

    def introspect(self) -> DatabaseSchema:
        inspector = inspect(self.engine)

        tables: list[TableInfo] = []

        for table_name in inspector.get_table_names():
            columns = [
                ColumnInfo(
                    name=column["name"],
                    type=str(column["type"]),
                    nullable=bool(column.get("nullable", True)),
                    primary_key=bool(column.get("primary_key", False)),
                )
                for column in inspector.get_columns(table_name)
            ]

            foreign_keys = [
                ForeignKeyInfo(
                    constrained_columns=list(foreign_key.get("constrained_columns") or []),
                    referred_table=str(foreign_key.get("referred_table")),
                    referred_columns=list(foreign_key.get("referred_columns") or []),
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
                if foreign_key.get("referred_table")
            ]

            tables.append(
                TableInfo(
                    name=table_name,
                    columns=columns,
                    foreign_keys=foreign_keys,
                )
            )

        return DatabaseSchema(tables=tables)