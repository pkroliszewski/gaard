from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class OutputClassification(StrEnum):
    PERSONAL_DATA = "personal_data"
    SENSITIVE_DATA = "sensitive_data"
    TECHNICAL_DATA = "technical_data"
    NEUTRAL_DATA = "neutral_data"
    UNKNOWN = "unknown"


class QueryIntentDecision(StrEnum):
    READ_ONLY_DATA_QUESTION = "read_only_data_question"
    WRITE_OR_MUTATION_REQUEST = "write_or_mutation_request"
    NON_DATA_REQUEST = "non_data_request"
    AMBIGUOUS = "ambiguous"


class QueryIntentClassification(BaseModel):
    decision: QueryIntentDecision = QueryIntentDecision.AMBIGUOUS
    confidence: float = 0.0
    reason: str = ""
    model_response: dict[str, Any] = Field(default_factory=dict)


class ContextMode(StrEnum):
    AUTO = "auto"
    NEW = "new"
    OFF = "off"


class ConversationContextDecision(StrEnum):
    NEW_TOPIC = "new_topic"
    FOLLOW_UP = "follow_up"
    AMBIGUOUS = "ambiguous"


class ConversationContextClassification(BaseModel):
    decision: ConversationContextDecision = ConversationContextDecision.NEW_TOPIC
    confidence: float = 0.0
    standalone_question: str = ""
    reason: str = ""
    model_response: dict[str, Any] = Field(default_factory=dict)
    prompt: dict[str, Any] = Field(default_factory=dict)
    source: str = ""


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    datasource_id: str = "default"
    datasource_ids: list[str] = Field(default_factory=list)
    user_id: str = "local-admin"
    interpret: bool = True
    conversation_id: str | None = None
    context_mode: ContextMode = ContextMode.AUTO


class GeneratedSql(BaseModel):
    sql: str
    confidence: float = 0.0
    assumptions: list[str] = Field(default_factory=list)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]


class QueryResponse(BaseModel):
    question: str
    answer: str
    sql: str
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)
