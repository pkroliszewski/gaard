from gaard_core.query_pipeline.models import QueryRequest, QueryResult


class MockResultInterpreter:
    def interpret(
        self,
        request: QueryRequest,
        result: QueryResult,
        sql: str = "",
    ) -> str:
        if not result.rows:
            return "Zapytanie nie zwróciło żadnych wyników."

        first_row = result.rows[0]

        if "active_patients_count" in first_row:
            return f"W bazie znajduje się {first_row['active_patients_count']} aktywnych pacjentów."

        if "patients_count" in first_row:
            return f"W bazie znajduje się {first_row['patients_count']} pacjentów."

        if "appointments_count" in first_row:
            return f"W bazie znajduje się {first_row['appointments_count']} wizyt."

        return f"Zapytanie zwróciło wynik: {first_row}."