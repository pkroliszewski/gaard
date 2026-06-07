from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class InvestigationRoute(StrEnum):
    SQL = "sql"
    ANALYSIS = "analysis"


class InvestigationContext(BaseModel):
    question: str = Field(min_length=1)
    datasource_id: str = "default"
    user_id: str = "local-admin"
    formatted_schema: str = ""
    business_logic: str = ""


class RequiredAnalysisTask(BaseModel):
    missing_information: str = ""
    required_analysis: str = ""
    category: str = "unknown"
    expected_output: str = ""


class InvestigationReadinessDecision(BaseModel):
    ready_for_sql: bool = False
    route: InvestigationRoute = InvestigationRoute.ANALYSIS
    confidence: float = 0.0
    reason: str = ""
    missing_information: list[str] = Field(default_factory=list)
    required_analysis: list[str] = Field(default_factory=list)
    required_analysis_tasks: list[RequiredAnalysisTask] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    model_response: dict[str, Any] = Field(default_factory=dict)


class InvestigationIteration(BaseModel):
    iteration: int
    agent: str
    decision: InvestigationReadinessDecision


class InvestigationLoopConfig(BaseModel):
    max_iterations: int = Field(default=1, ge=1)
    readiness_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)


class InvestigationLoopResult(BaseModel):
    route: InvestigationRoute
    ready_for_sql: bool
    max_iterations: int
    confidence_threshold: float
    iterations: list[InvestigationIteration] = Field(default_factory=list)

    @property
    def final_decision(self) -> InvestigationReadinessDecision | None:
        if not self.iterations:
            return None

        return self.iterations[-1].decision
