from typing import Any

from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    QueryRequest,
)


class MockConversationContextClassifier:
    """Offline mode does not attempt semantic classification without an LLM."""

    def classify(
        self, request: QueryRequest, conversation_context: dict[str, Any]
    ) -> ConversationContextClassification:
        return ConversationContextClassification(
            decision=ConversationContextDecision.NEW_TOPIC,
            standalone_question=request.question,
            reason="Context classification is disabled in mock mode.",
            source="mock",
        )

    def summarize(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
        classification: ConversationContextClassification,
    ) -> ConversationContextClassification:
        return classification
