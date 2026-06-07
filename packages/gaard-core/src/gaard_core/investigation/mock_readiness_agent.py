from gaard_core.investigation.models import (
    InvestigationContext,
    InvestigationReadinessDecision,
    InvestigationRoute,
)


class MockInvestigationReadinessAgent:
    name = "mock_investigation_readiness"

    def __init__(self, decision: InvestigationReadinessDecision | None = None) -> None:
        self.decision = decision or InvestigationReadinessDecision(
            ready_for_sql=True,
            route=InvestigationRoute.SQL,
            confidence=1.0,
            reason="Mock readiness agent allows the normal SQL pipeline.",
        )

    def assess(self, context: InvestigationContext) -> InvestigationReadinessDecision:
        return self.decision
