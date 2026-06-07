# Security Model

GAARD is a governed AI access layer for relational data.

Investigation mode separates two security concerns:

- Exploration: read-only queries may be used to understand the current data
  world, within the permissions of the configured technical datasource user.
  These steps must be fully audited.
- Final answer: the candidate final SQL and answer plan must pass the existing
  SQL validation, governance checks, and epistemic readiness gate before
  execution and user-facing output.

This means exploratory analysis should not be blocked merely because it is
exploratory, but no final answer may bypass validation, policy, or governance.
No final answer may rely on `model_inference_only` claims unless that limitation
is explicitly visible to the user.

The audit trail is part of the security model. It records the operational
reasoning path: goal, hypotheses, SQL, observations, confidence changes,
uncertainties, claim/evidence state before and after each step, readiness
decisions, errors, and candidate business knowledge.
