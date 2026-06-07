from gaard_core.query_pipeline.models import GeneratedSql, QueryRequest


class MockSqlGenerator:
    def generate(self, request: QueryRequest) -> GeneratedSql:
        question = request.question.lower()

        if "aktywn" in question or "active" in question:
            return GeneratedSql(
                sql="SELECT COUNT(*) AS active_patients_count FROM patients WHERE status = 'active'",
                confidence=0.95,
                assumptions=["Using demo patients table and status = active."],
            )

        if "pacjent" in question or "patient" in question:
            return GeneratedSql(
                sql="SELECT COUNT(*) AS patients_count FROM patients",
                confidence=0.95,
                assumptions=["Using demo patients table."],
            )

        if "wizyt" in question or "appointment" in question:
            return GeneratedSql(
                sql="SELECT COUNT(*) AS appointments_count FROM appointments",
                confidence=0.9,
                assumptions=["Using demo appointments table."],
            )

        return GeneratedSql(
            sql="SELECT 1 AS value",
            confidence=0.5,
            assumptions=["Fallback mock query."],
        )