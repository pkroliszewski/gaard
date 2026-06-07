# Query Pipeline

GAARD exposes two query modes through the same `QueryResponse` contract:

- `sql`: the legacy direct read-only SQL pipeline.
- `investigation`: an audited analytical investigation mode.

Investigation mode is not classic text-to-SQL. Its target behavior is an epistemic
loop over an answer state:

1. Read the current answer state.
2. Identify the most important uncertainty.
3. Treat mappings, joins, filters, dictionary values, counts, and semantics as
   hypotheses.
4. Gather evidence through schema checks, business knowledge, or read-only
   exploratory SQL.
5. Record the step in the investigation audit trail.
6. Run a readiness gate before final SQL and before final answer composition.
7. Continue until the answer is sufficiently evidenced, clarification is needed,
   a technical failure occurs, or a configured limit is reached.

Exploratory SQL and final SQL have different jobs:

- Exploratory SQL reduces uncertainty. It must be read-only and audited.
- Final SQL produces the user-facing result. It must pass the governed task SQL
  validation, the active governance policy, and epistemic readiness before
  execution.
- Final answer composition can use only claims that are observed, verified, or
  explicitly surfaced as assumptions with user-visible limitations.

The API response remains backward compatible. New investigation details are
added under `metadata`, including `investigation_audit_trail`,
`epistemic_claims`, `epistemic_evidence`, `final_answer_readiness`, and
candidate business knowledge.
