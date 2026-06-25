## Detailed GAARD Configuration

The API runtime is configured through the admin UI and persisted in the SQLite
metadata database at `metadata.db`. The metadata database is the only source of
truth for prompt templates, datasource connectors, LLM settings, runtime modes,
query limits, audit retention, and schema cache settings.

The first startup seeds these defaults:

| Setting | Default |
| --- | --- |
| Metadata database | `sqlite:///./metadata.db` |
| Default datasource | `sqlite:///./examples/medical-poc/demo.db` |
| SQL dialect | `sqlite` |
| Intent classification | `auto` |
| SQL generation | `llm` |
| Result interpretation | `llm` |
| Output classification | `auto` |
| LLM provider | `openai-compatible` |
| LLM base URL | `https://api.openai.com/v1` |
| LLM API key | `change-me` |
| LLM model | `gpt-4.1-mini` |
| LLM timeout | `60` seconds |
| LLM extra body | `{}` |
| Query max rows | `100` |
| Query timeout | `30` seconds |
| Schema cache TTL | `300` seconds |
| Audit retention | `90` days |

Change these values in `/admin`; GAARD will keep using the metadata-db values
after restarts.

For automated tests or local development without an LLM key, you can seed
different initial values with process environment variables before the API
creates metadata settings, for example:

```bash
GAARD_SQL_GENERATION_MODE=mock GAARD_RESULT_INTERPRETATION_MODE=mock python -m uvicorn gaard_api.main:app --reload --host 0.0.0.0 --port 8000
```

GAARD does not load `.env` files. Environment variables only affect code
defaults and system-seeded metadata settings; admin-edited metadata settings
remain authoritative.