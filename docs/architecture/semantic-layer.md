# Semantic Layer And Business Knowledge

GAARD treats semantic knowledge as evidence, not dogma.

Final SQL and final answers are governed by epistemic claim status and
provenance. A semantic claim must be marked as one of:

- `hypothesis`
- `observed`
- `verified`
- `rejected`
- `inconclusive`
- `assumed_with_user_visible_limitation`

Its provenance must also be explicit: user input, schema metadata, database
observation, approved business knowledge, a previous verified investigation, or
model inference only.

Business knowledge can come from:

- approved admin-maintained business logic,
- schema metadata,
- prior audited investigation steps,
- newly discovered candidate knowledge from investigation.

A business-knowledge claim should include:

- knowledge type,
- claim text,
- datasource,
- related tables, columns, and values,
- evidence,
- confidence,
- status such as `candidate`, `verified`, `stale`, or `rejected`,
- creation and verification timestamps,
- source and audit/request reference,
- whether admin or data-steward approval is required.

Previously learned knowledge may reduce the cost of investigation, but it does
not eliminate verification. If final correctness depends on prior knowledge, the
agent should run a lightweight sanity check such as verifying that the table,
column, or dictionary value still exists.

Zero and empty SQL results are not automatically business conclusions. They can
support a final answer only when table meaning, filter meaning, join semantics,
and counting method have enough evidence, or when the limitation is visible to
the user.
