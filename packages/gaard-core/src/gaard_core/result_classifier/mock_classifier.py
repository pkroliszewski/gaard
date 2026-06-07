from gaard_core.query_pipeline.models import OutputClassification, QueryRequest


class MockResultClassifier:
    def classify(
        self,
        request: QueryRequest,
        answer: str,
    ) -> OutputClassification:
        return OutputClassification.NEUTRAL_DATA
