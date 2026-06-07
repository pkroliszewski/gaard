from gaard_core.query_pipeline.models import QueryResult


class MockQueryExecutor:
    def execute(self, sql: str) -> QueryResult:
        normalized = sql.lower()

        if "patients" in normalized:
            return QueryResult(
                columns=["patients_count"],
                rows=[
                    {
                        "patients_count": 124,
                    }
                ],
            )

        return QueryResult(
            columns=["value"],
            rows=[
                {
                    "value": 1,
                }
            ],
        )