# gaard-extract

Private GAARD extension for configuring unstructured source models.

Implemented configuration scope:

- select an existing GAARD datasource;
- read its already-introspected schema;
- choose the main table;
- map `case_id` and `content` roles per table.
- choose chunking mode for downstream Extract processing;
- configure a dedicated OpenAI-compatible embedding endpoint;
- define the LLM extraction blueprint:
  - active source scope inherited from `Source`;
  - chunk selection and optional embedding-neighbor context;
  - information types and fields;
  - global extraction rules;
  - review thresholds;
  - JSON Schema preview/manual override.

This package intentionally does not ingest data, chunk text, call LLMs, or publish
canonical models yet.
