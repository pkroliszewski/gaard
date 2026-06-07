from typing import Any

from pydantic import BaseModel, Field

from gaard_core.schema.models import DatabaseSchema


class SqlGenerationPromptRequest(BaseModel):
    question: str = Field(min_length=1)
    database_schema: DatabaseSchema | None = None
    formatted_schema: str | None = None
    dialect: str = "sqlite"
    max_rows: int = 100


class CompiledPrompt(BaseModel):
    system_prompt: str
    user_prompt: str
    metadata: dict[str, Any] = Field(default_factory=dict)
