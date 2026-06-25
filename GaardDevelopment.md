## Gaard Development

Use this path when you want to run the API directly on your machine without
Docker.
### 1. Clone the repository

```bash
git clone https://github.com/pkroliszewski/gaard.git
cd gaard
```

If you already have the project locally, go to the repository root before
running the next commands.

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

This installs local GAARD packages in editable mode:

- `packages/gaard-core`
- `packages/gaard-connectors`
- `packages/gaard-llm`
- `packages/gaard-api`
- `packages/gaard-client`

### 4. Prepare the demo datasource

Create the bundled Medical POC SQLite demo database:

```bash
gaard-core install-example-database
```

By default this writes `examples/medical-poc/demo.db` in the current working
directory, registers it as the active `default` datasource in `metadata.db`,
and matches the default `GAARD_DATASOURCE_URL`. To place the database elsewhere,
pass `--output /path/to/demo.db`; the command prints the datasource URL it saved
in metadata.

Runtime configuration is seeded into `metadata.db` when the API starts or the sample database is created.

### 5. Start the API & Client

Run this from the repository root so relative datasource paths resolve correctly:

```bash
gaard-core start --host 0.0.0.0 --port 8000
gaard-client start --host 0.0.0.0 --port 8001
```

### 6. Check health

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/health
```

## Query Endpoint

Send a natural-language question to:

```text
POST /api/v1/query
```

Example:

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How many active patients are there?"}'
```

The response shape is:

```json
{
  "question": "How many active patients are there?",
  "answer": "...",
  "sql": "...",
  "rows": [],
  "metadata": {
    "datasource_id": "default",
    "user_id": "local-admin",
    "confidence": 0.0,
    "mode": "llm"
  }
}
```

Exact SQL, answer text, rows, confidence, and duration metadata depend on the
configured model, datasource, and query.

## Admin Portal

The admin portal is served by the API at:

```text
http://localhost:8000/admin
```

The default bootstrap administrator is:

```text
username: admin
password: admin
```

The first login requires a password change before the rest of the admin portal
can be used.

The current admin surface includes:

- administrator login and password change
- data query audit log with retention settings
- prompt template management stored in metadata
- schema cache TTL management and invalidation
- placeholder modules for identity, datasource connectors, SQL validation rules, result interpretation policies, and licensing
- admin audit events for management actions

## Community Client

The community client is a separate Uvicorn app named `client`. It provides a
chat-style UI for asking questions against the GAARD backend.

Start the API first, then run the client from the repository root:

```bash
gaard-client start --host 0.0.0.0 --port 8001
```

Open:

```text
http://localhost:8001?backendUrl=http://localhost:8000
```

The client sends questions to the backend's default query endpoint:

```text
POST /api/v1/query
```

Only the backend URL is configurable for the client. You can set it per browser
session with the `backendUrl` query parameter or provide a default with:

```env
GAARD_CLIENT_BACKEND_URL=http://localhost:8000
```

After each question, the client shows the question, answer, processing time,
`datasource_id`, and `output_classification`. The input stays fixed at the
bottom while previous answers remain visible as history.
