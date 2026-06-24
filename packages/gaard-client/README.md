# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using natural language while keeping SQL generation, validation, execution, prompts, connectors, and auditability under control.

For more informacion see https://github.com/pkroliszewski/gaard

# This package

`gaard-client` provides the community web client for `gaard-api`.

After installation, start it with:

```bash
gaard-client start
```

The command accepts `--host`, `--port`, and `--reload`. By default the client is
available at `http://localhost:8001?backendUrl=http://localhost:8000`.

The older `gaard client` command remains available for compatibility.
