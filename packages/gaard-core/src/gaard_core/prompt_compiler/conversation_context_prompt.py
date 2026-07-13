from typing import Any

from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import ConversationContextDecision, QueryRequest


class ConversationContextPromptCompiler:
    def compile(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> CompiledPrompt:
        recent_turns = self._recent_turns(conversation_context)
        payload = {
            "turn_t_minus_2": recent_turns[0] if len(recent_turns) == 2 else {},
            "turn_t_minus_1": recent_turns[-1] if recent_turns else {},
            "turn_t": {
                "question": request.question,
                "datasource_id": request.datasource_id,
                "datasource_ids": request.datasource_ids,
            },
        }

        return CompiledPrompt(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(payload),
            metadata={
                "allowed_decisions": [item.value for item in ConversationContextDecision],
                "decision_task": "logical_continuation_yes_no",
            },
        )

    def _recent_turns(self, conversation_context: dict[str, Any]) -> list[dict[str, Any]]:
        turns = [
            turn for turn in conversation_context.get("turns", []) if isinstance(turn, dict)
        ][-2:]
        labels = ["t-2", "t-1"] if len(turns) == 2 else ["t-1"]
        return [
            {
                "label": label,
                "question": str(turn.get("question") or ""),
                "standalone_question": str(turn.get("standalone_question") or ""),
                "answer": str(turn.get("answer") or ""),
                "sql": str(turn.get("sql") or ""),
                "context_decision": str(turn.get("context_decision") or ""),
                "context_reason": str(turn.get("context_reason") or ""),
            }
            for label, turn in zip(labels, turns, strict=False)
        ]

    def _build_system_prompt(self) -> str:
        return """You are GAARD Conversation Context Classification.

Your task is to decide whether the current user data question (turn t) is a
logical continuation of the recent conversation turns (t-2 and t-1).

Use the previous question-answer pairs as conversation evidence. Do not classify
by rigid prefix or keyword rules. Decide semantically whether turn t depends on,
compares with, narrows, broadens, or otherwise continues the immediately
preceding analytical thread.

Allowed decisions:
- new_topic: answer "no" to logical continuation. The question starts a new analytical thread.
- follow_up: answer "yes" to logical continuation. The question continues the
  recent thread and can be executed safely.
- ambiguous: the question appears to continue the thread, but required entities,
  filters, date ranges, or datasource scope cannot be inferred safely.

Decision rules:
1. First answer the yes/no question: is turn t a logical continuation of t-1/t-2?
2. A question can be a logical continuation even when it is already
   self-contained. In that case use follow_up, set
   current_question_is_standalone to true, and set standalone_question to the
   current question.
3. If the answer is no, use new_topic and set standalone_question to the current question.
4. If the answer is yes and the current question is elliptical, rewrite it as a
   standalone data question using t-1/t-2.
5. Use ambiguous only when the answer is yes but the continuation cannot be
   rewritten or executed safely without asking the user.
6. Treat detail/projection requests about the previous result as follow_up when
   the previous turn defines the result set. If the previous question counted,
   grouped, or filtered records and the user now asks for descriptions, names,
   statuses, fields, details, or values for those same records, rewrite by
   preserving the previous filters/date range/datasource and changing only the
   returned fields.
7. Do not mark a projection/detail follow-up ambiguous merely because the
   previous answer did not expose row ids. The previous standalone question and
   SQL are enough context for the next SQL generation step.
8. Never include rows or sensitive data in the standalone question.

Output rules:
- Return only a JSON object.
- Do not include markdown.
- Do not include reasoning outside the JSON.
- Do not include <think> blocks.
- Use exactly this JSON shape:
  {
    "is_continuation": false,
    "decision": "new_topic",
    "current_question_is_standalone": true,
    "confidence": 0.0,
    "standalone_question": "rewritten or current question",
    "reason": "short reason"
  }
"""

    def _build_user_prompt(self, payload: dict[str, Any]) -> str:
        return f"""Decide whether turn t is a logical continuation of turns t-2 and t-1.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return one JSON object with:
- is_continuation: boolean yes/no answer to the logical-continuation question
- decision: one of {", ".join(item.value for item in ConversationContextDecision)}
- current_question_is_standalone: boolean
- confidence: number from 0 to 1
- standalone_question: required for follow_up and new_topic; empty only for ambiguous
- reason: short explanation
"""
