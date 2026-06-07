from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import OutputClassification, QueryRequest


class ResultClassificationPromptCompiler:
    def compile(
        self,
        request: QueryRequest,
        answer: str,
    ) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "answer": answer,
        }

        return CompiledPrompt(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(payload),
            metadata={
                "allowed_classifications": [
                    item.value for item in OutputClassification
                ],
            },
        )

    def _build_system_prompt(self) -> str:
        return """You are GAARD Output Classification.

Your task is to classify the user-facing interpreted answer into exactly one output data class.

Allowed classes:
- personal_data: the answer is about personal data, people, identities, audit events concerning personal data, or aggregates describing personal data access.
- sensitive_data: the answer is about sensitive or special-category data such as health, credentials, secrets, financial risk, legal status, biometric data, or similarly high-risk information.
- technical_data: the answer is about system configuration, schemas, logs, query mechanics, infrastructure, or operational technical metadata.
- neutral_data: the answer is about non-personal, non-sensitive business or aggregate information.
- unknown: the answer cannot be classified reliably.

Priority rules:
1. Choose sensitive_data over personal_data if both apply.
2. Choose personal_data over technical_data if the answer concerns audit or technical records about personal data.
3. Choose unknown instead of guessing when the answer lacks enough context.

Rules:
- Classify only the interpreted answer and the user's question.
- Do not classify raw database rows.
- Return only one allowed class value.
- Do not include explanations.
- Do not include markdown.
- Do not include reasoning.
- Do not include <think> blocks.
"""

    def _build_user_prompt(self, payload: dict[str, str]) -> str:
        return f"""Classify this interpreted result.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return exactly one of:
{", ".join(item.value for item in OutputClassification)}
"""
