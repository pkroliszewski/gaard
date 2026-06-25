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
