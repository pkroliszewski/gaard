from typing import Protocol

from gaard_core.investigation.models import (
    InvestigationContext,
    InvestigationIteration,
    InvestigationLoopConfig,
    InvestigationLoopResult,
    InvestigationReadinessDecision,
    InvestigationRoute,
)


class InvestigationReadinessAgent(Protocol):
    name: str

    def assess(self, context: InvestigationContext) -> InvestigationReadinessDecision:
        pass


class InvestigationLoop:
    def __init__(
        self,
        readiness_agent: InvestigationReadinessAgent,
        config: InvestigationLoopConfig | None = None,
    ) -> None:
        self.readiness_agent = readiness_agent
        self.config = config or InvestigationLoopConfig()

    def run(self, context: InvestigationContext) -> InvestigationLoopResult:
        iterations: list[InvestigationIteration] = []

        for iteration_number in range(1, self.config.max_iterations + 1):
            decision = self.readiness_agent.assess(context)
            normalized_decision = self._normalize_decision(decision)
            iterations.append(
                InvestigationIteration(
                    iteration=iteration_number,
                    agent=self.readiness_agent.name,
                    decision=normalized_decision,
                )
            )

            if normalized_decision.route == InvestigationRoute.SQL:
                return InvestigationLoopResult(
                    route=InvestigationRoute.SQL,
                    ready_for_sql=True,
                    max_iterations=self.config.max_iterations,
                    confidence_threshold=self.config.readiness_confidence_threshold,
                    iterations=iterations,
                )

            return InvestigationLoopResult(
                route=InvestigationRoute.ANALYSIS,
                ready_for_sql=False,
                max_iterations=self.config.max_iterations,
                confidence_threshold=self.config.readiness_confidence_threshold,
                iterations=iterations,
            )

        return InvestigationLoopResult(
            route=InvestigationRoute.ANALYSIS,
            ready_for_sql=False,
            max_iterations=self.config.max_iterations,
            confidence_threshold=self.config.readiness_confidence_threshold,
            iterations=iterations,
        )

    def _normalize_decision(
        self,
        decision: InvestigationReadinessDecision,
    ) -> InvestigationReadinessDecision:
        ready = (
            decision.ready_for_sql
            and decision.confidence >= self.config.readiness_confidence_threshold
        )
        route = InvestigationRoute.SQL if ready else InvestigationRoute.ANALYSIS

        return decision.model_copy(
            update={
                "ready_for_sql": ready,
                "route": route,
            }
        )
