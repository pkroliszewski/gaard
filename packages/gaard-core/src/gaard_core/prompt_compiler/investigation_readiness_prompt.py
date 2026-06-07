from gaard_core.investigation.models import InvestigationContext, InvestigationRoute
from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt


class InvestigationReadinessPromptCompiler:
    def compile(self, context: InvestigationContext) -> CompiledPrompt:
        payload = {
            "question": context.question,
            "datasource_id": context.datasource_id,
            "user_id": context.user_id,
            "schema": context.formatted_schema,
            "business_logic": context.business_logic,
        }

        return CompiledPrompt(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(payload),
            metadata={
                "allowed_routes": [item.value for item in InvestigationRoute],
            },
        )

    def _build_system_prompt(self) -> str:
        return """You are GAARD Investigation Readiness.

Your task is to decide whether GAARD already knows enough to create a correct SQL query for the user's question.

Assume nothing. Verify continuously.

Use only:
- the user's question,
- the active datasource schema,
- the approved or previously saved business logic supplied in the payload.

You do not generate SQL.
You do not answer the user.
You decide only whether normal SQL generation may start safely.

Return ready_for_sql=true only when all information needed for correct SQL is explicit in the question, schema, and business logic:
- requested business entity or metric,
- relevant tables and columns,
- required filters and dictionary/status values,
- required joins or relationships,
- requested output shape such as count, list, detail, or aggregation.

Return ready_for_sql=false when any material element is missing, ambiguous, inferred only from the model, or would require checking data values before SQL can be trusted. In that case route must be analysis.

Output rules:
- Return only a JSON object.
- Do not include markdown.
- Do not include reasoning outside the JSON.
- Do not include <think> blocks.
- Use exactly this JSON shape:
  {"ready_for_sql":false,"route":"analysis","confidence":0.0,"reason":"short reason","missing_information":[],"required_analysis":[],"required_analysis_tasks":[],"assumptions":[]}

Required analysis task shape:
{"missing_information":"what is missing","required_analysis":"specific read-only data question for SQL analysis","category":"dictionary_value","expected_output":"what kind of result would resolve this"}

Allowed categories:
- dictionary_value
- relationship_logic
- filter_logic
- aggregation_logic
- entity_mapping
- unknown
"""

    def _build_user_prompt(self, payload: dict[str, str]) -> str:
        return f"""Assess whether normal SQL generation can start.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return one JSON object with:
- ready_for_sql: boolean
- route: sql or analysis
- confidence: number from 0 to 1
- reason: short explanation
- missing_information: list of missing or ambiguous items
- required_analysis: list of checks that Analysis mode should perform when ready_for_sql=false
- required_analysis_tasks: list of structured SQL-analysis tasks with missing_information, required_analysis, category, expected_output
- assumptions: list of any assumptions that would affect SQL correctness
"""
