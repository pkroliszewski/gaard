from gaard_core.errors import ConfigurationError
from gaard_core.prompt_compiler.models import CompiledPrompt, SqlGenerationPromptRequest
from gaard_core.prompt_compiler.schema_formatter import SchemaPromptFormatter


class SqlGenerationPromptCompiler:
    def __init__(self, schema_formatter: SchemaPromptFormatter | None = None) -> None:
        self.schema_formatter = schema_formatter or SchemaPromptFormatter()

    def compile(self, request: SqlGenerationPromptRequest) -> CompiledPrompt:
        formatted_schema = self._resolve_formatted_schema(request)

        system_prompt = self._build_system_prompt(
            dialect=request.dialect,
            max_rows=request.max_rows,
        )

        user_prompt = self._build_user_prompt(
            schema=formatted_schema,
            question=request.question,
        )

        return CompiledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "dialect": request.dialect,
                "max_rows": request.max_rows,
                "schema_source": "formatted_schema"
                if request.formatted_schema is not None
                else "database_schema",
                "tables_count": len(request.database_schema.tables)
                if request.database_schema is not None
                else None,
            },
        )

    def _resolve_formatted_schema(self, request: SqlGenerationPromptRequest) -> str:
        if request.formatted_schema is not None:
            return request.formatted_schema

        if request.database_schema is None:
            raise ConfigurationError("Either database_schema or formatted_schema must be provided.")

        return self.schema_formatter.format(request.database_schema)

    def _build_system_prompt(self, dialect: str, max_rows: int) -> str:
        return f"""You are an expert data analyst and SQL specialist.

Your task is to generate exactly one valid SQL SELECT query based on:
- the user's question,
- the provided database schema,
- the provided data rules and descriptions.

You must generate SQL for the {dialect} dialect.

Core rules:
1. Generate only one SQL statement.
2. Generate only a SELECT statement.
3. Do not generate multiple statements.
4. Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, MERGE, REPLACE, GRANT or REVOKE.
5. Use only tables, views and columns listed in the provided schema.
6. Do not invent tables, views or columns.
7. Return only raw SQL.
8. Do not use markdown.
9. Do not use code fences.
10. Do not add comments.
11. Do not add explanations.
12. Do not include reasoning.
13. Do not include <think> blocks.

Query construction rules:
1. If the user asks for a count, use COUNT with a clear alias.
2. If the user asks for a breakdown, distribution, comparison by category, or values "by" some dimension, use one SELECT statement with GROUP BY or conditional aggregation.
3. If the user asks for both a total and a breakdown, prefer one SELECT statement that returns grouped rows or conditional aggregate columns.
4. Do not solve one user question by generating multiple separate SELECT statements.
5. Prefer explicit column names over SELECT *.
6. Add LIMIT {max_rows} when the query may return many rows.
7. Do not add LIMIT to pure aggregate queries that return a single row, unless it is already useful for the dialect or safety.
8. Use clear aliases for computed expressions.
9. When the query uses more than one table, every table must have a short, stable alias.
10. When the query uses more than one table, every column reference must be qualified with the correct table alias in SELECT, JOIN, WHERE, GROUP BY, HAVING and ORDER BY.
11. When the query uses table aliases, use those aliases consistently and do not mix aliased and unaliased table references.
12. Do not use unqualified column names in joins or multi-table queries.
13. If the question is ambiguous, choose the most likely interpretation based on the schema, column names, descriptions and data rules.
14. Do not use bind parameters, placeholders, variables, or prepared-statement markers such as :name, ?, $1, @name, or %(name)s.
15. When dates or dynamic ranges are needed, express them directly with {dialect} SQL functions or literal values so the SQL is executable without any external parameter binding.

Output contract:
- Return exactly one SQL SELECT statement.
- The first non-whitespace token must be SELECT or WITH.
- The final output must be executable SQL only.
- The final SQL must be self-contained and executable as-is.
"""

    def _build_user_prompt(self, schema: str, question: str) -> str:
        return f"""Database schema:
{schema}

User question:
{question}

Generate exactly one SQL SELECT statement for this question.
If the answer requires multiple values, categories or groups, return them using one query.
Return SQL only.
"""
