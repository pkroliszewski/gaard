# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using natural language while keeping SQL generation, validation, execution, prompts, connectors, and auditability under control.

For more informacion see https://github.com/pkroliszewski/gaard

# This package

`gaard-api` provides the GAARD FastAPI backend and bundled admin application.

After installation, start it with:

```bash
gaard-core install-example-database
gaard-core start
```

`gaard-core install-example-database` creates the bundled Medical POC SQLite
database at `examples/medical-poc/demo.db` in the current working directory,
registers it as the active `default` datasource in `metadata.db`, and matches
the default datasource URL. Use
`gaard-core install-example-database --output /path/to/demo.db` to place it
elsewhere; the command prints the SQLite datasource URL saved in metadata.

`gaard-core start` accepts `--host`, `--port`, and `--reload`. By default the
API is available at `http://localhost:8000` and the admin application at
`http://localhost:8000/admin`.

`gaard-api start` is an alias. `gaard admin` remains available for compatibility.

## Conversation context

With `context_mode: "auto"`, each question stores a `new_topic` or `follow_up`
decision. The LLM receives all turns since the last `new_topic`, including that
question. For a follow-up, a separate LLM call compresses this context together
with the current question into one standalone sentence. Both calls use
temperature zero. The resulting sentence is saved before SQL or Analysis runs;
a new topic uses the current question as its context.

`GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/context` returns
the saved execution-time snapshot as JSON with `context`, `context_decision`,
`conversation_id`, and `turn_id`. The IDs are available in the query response's
`metadata.conversation` object. This authenticated endpoint only allows the
conversation owner and never regenerates the context from later messages.

`context_mode: "new"` starts a new conversation; `"off"` remains stateless.
Mock mode does not perform semantic context classification.
