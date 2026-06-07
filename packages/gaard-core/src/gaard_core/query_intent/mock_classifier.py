from gaard_core.query_pipeline.models import (
    QueryIntentClassification,
    QueryIntentDecision,
    QueryRequest,
)


class MockQueryIntentClassifier:
    def classify(self, request: QueryRequest) -> QueryIntentClassification:
        return QueryIntentClassification(
            decision=QueryIntentDecision.READ_ONLY_DATA_QUESTION,
            confidence=1.0,
            reason="Mock intent classifier allows the request.",
        )
