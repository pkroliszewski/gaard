# Investigation-scoped findings

GAARD exposes semantic findings produced by an Analysis session (the current
implementation of an Investigation) without treating them as globally active
Business Logic. The contract version implemented here is `1.0`.
The Radar decision port is available in `gaard-api` 0.2.16 and newer.

## Lifecycle

1. An Analysis database observation produces a concise semantic finding.
2. GAARD persists the finding in `analysis_findings` and keeps the existing
   `BusinessLogicSuggestion` in its normal `pending`, `enabled=false` state.
3. Radar reads new findings and submits a decision to the Cognitive Runtime port.
4. `accept_for_investigation` makes the finding available only to later planner
   and SQL-generation steps of that Analysis session.
5. Every context inclusion is recorded separately from an explicit planner
   declaration such as `used_for_query` or `used_for_hypothesis`. Query-audit
   metadata also contains `analysis_working_finding_ids`.
6. A later observation can mark the finding `strengthened`, `weakened`, or
   `contradicted`. Contradicted findings are removed from active working knowledge.
7. `withdraw_for_investigation` and `reject_for_investigation` remove the finding
   from active working knowledge. The audit history remains available.

Working acceptance is tied to the retained Analysis record rather than a wall
clock. It is functionally inactive while the session is completed, but is
restored if that exact technical session is explicitly resumed through its
message endpoint. It is never copied to a new session. Findings and decisions
remain available afterward as audit records.

`accept_as_persistent_business_logic` is deliberately separate. It requires an
authenticated GAARD administrator and delegates to the existing Business Logic
activation and admin-audit flow.

## API

The API extends the existing `/api/v1/analysis` surface:

- `GET /api/v1/analysis/{session_id}/findings?after=0`
- `POST /api/v1/analysis/{session_id}/finding-decisions`
- `GET /api/v1/analysis/{session_id}/working-knowledge`
- `POST /api/v1/analysis/{session_id}/findings/{finding_id}/evidence`

The older additive MVP endpoint
`PUT /api/v1/analysis/{session_id}/findings/{finding_id}/decision` remains
available as a compatibility adapter. Radar should use `POST
/finding-decisions`.

`after` is an opaque increasing cursor returned as `next_cursor`. Radar
decisions are durable rows in `analysis_finding_decisions`. Their idempotency key
is derived from the technical session ID, finding ID, Radar run ID, and decision. A
retry returns the original `decision_id` and `"idempotent": true`.

Example finding:

```json
{
  "finding_id": "43f046d62e6c4673aeefb880f6b53f5d",
  "investigation_id": "9f54b263a33c490496254b6346c4501b",
  "statement": "The dictionary value corresponding to Kardiologia is cardiology.",
  "finding_type": "semantic_mapping",
  "confidence": 0.94,
  "critique": "The mapping was confirmed only in the current datasource.",
  "scope": {
    "source": "nfz",
    "datasource_id": "nfz",
    "entity": "specialization",
    "field": "specialization_name"
  },
  "evidence_refs": ["query:dictionary-check"],
  "status": "pending",
  "contract_version": "1.0"
}
```

Example session-only decision:

```json
{
  "finding_id": "43f046d62e6c4673aeefb880f6b53f5d",
  "decision": "accept_for_investigation",
  "confidence": 0.93,
  "verdict": "The observed source values support this mapping.",
  "scope": {
    "investigation_id": "9f54b263a33c490496254b6346c4501b",
    "radar_run_id": "106c6b4dca20422f8363d13a21be7b0f"
  },
  "evidence_refs": ["gaard-audit:123"],
  "contract_version": "1.0"
}
```

Example response:

```json
{
  "decision_id": "2a76ed327cdd47b7bf74f998a175a64d",
  "finding_id": "43f046d62e6c4673aeefb880f6b53f5d",
  "decision": "accept_for_investigation",
  "investigation_id": "9f54b263a33c490496254b6346c4501b",
  "accepted": true,
  "persistent_business_logic_modified": false,
  "idempotent": false
}
```

The Radar endpoint accepts `accept_for_investigation`,
`reject_for_investigation`, and `withdraw_for_investigation`. The path session,
scope investigation, finding ownership, Radar run ID, confidence, and
namespaced evidence references are validated. Additional scope fields are
rejected so the decision cannot broaden the original server-side finding.

## Security and compatibility

Session ownership comes from the authenticated GAARD identity, not the
caller-provided query `user_id`. Findings are selected by both investigation and
owner. Server-authoritative datasource scope replaces any model-provided source.

Working findings are serialized as untrusted semantic evidence in the user-side
planner and SQL-generation context. They never become a system prompt and cannot
change datasource selection, identity/table filtering, SELECT-only validation,
governance, row limits, timeouts, or the executor.

Clients unaware of findings continue to receive the existing
`business_logic_suggestion` event and can ignore its new additive `finding` and
`finding_id` properties. The existing Admin UI and Business Logic endpoints keep
their previous behavior.

Usage and evidence updates are available in the finding payload and the
Analysis event stream as `finding_used` and `finding_evidence_updated`. A
contradiction keeps all earlier records, sets the finding to
`needs_reevaluation`, and deactivates the current Radar acceptance.

## MVP boundaries

Radar integration is polling-based; this change does not add a Radar network
integrator or a competing orchestrator. Decisions are normalized durable rows,
while evidence-update and usage histories remain JSON audit arrays on the
finding row and are not intended for high-contention concurrent writers. A
future version can normalize those remaining histories into append-only tables
and add explicit retention/archival jobs, calibrated confidence, and a
separately governed promotion workflow. Successful use never promotes a finding
automatically.
