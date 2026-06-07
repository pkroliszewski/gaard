from gaard_core.schema.models import DatabaseSchema, TableInfo


class SchemaPromptFormatter:
    def format(self, schema: DatabaseSchema) -> str:
        if not schema.tables:
            return "No tables available."

        sections: list[str] = []

        for table in sorted(schema.tables, key=lambda item: item.name):
            sections.append(self._format_table(table))

        return "\n\n".join(sections)

    def _format_table(self, table: TableInfo) -> str:
        lines: list[str] = [f"Table: {table.name}", "Columns:"]

        if not table.columns:
            lines.append("- No columns available.")
        else:
            for column in table.columns:
                modifiers: list[str] = []

                if column.primary_key:
                    modifiers.append("primary key")

                if not column.nullable:
                    modifiers.append("not null")

                modifier_text = f" ({', '.join(modifiers)})" if modifiers else ""
                lines.append(f"- {column.name}: {column.type}{modifier_text}")

        if table.foreign_keys:
            lines.append("Foreign keys:")

            for foreign_key in table.foreign_keys:
                constrained = ", ".join(foreign_key.constrained_columns)
                referred = ", ".join(foreign_key.referred_columns)
                lines.append(f"- {constrained} -> {foreign_key.referred_table}.{referred}")

        return "\n".join(lines)