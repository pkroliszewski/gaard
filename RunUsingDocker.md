## Run with Docker Compose

Docker is optional. Use this path when you want to run the API in a container
with local SQLite metadata storage.

To build images on one machine, transfer them, and run them with Podman on
another machine, see [Compiling Docker Images for Transfer](CompilingDockerImages.md).

GAARD stores admin users, prompt templates, audit logs, datasource connector
settings, schema cache entries, and admin settings in a local SQLite metadata
database. Persist the SQLite file outside the container if you want metadata to
survive container rebuilds and restarts.

### 1. Prepare the demo datasource

Generate the demo SQLite database before building the Docker image, because the
current API image copies the `examples/` directory at build time:

```bash
python examples/medical-poc/create_demo_db.py
```

Runtime configuration is created in the SQLite metadata database on first API
start and then managed from `/admin`.

### 2. Build and start services

If your environment uses Docker Compose v2:

```bash
docker compose up --build
```

If your environment uses standalone Docker Compose:

```bash
docker-compose up --build
```

The API should be available at:

```text
http://localhost:8000
```

### 3. Check health

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/health
```

Expected response:

```json
{
  "status": "ok"
}
```

### 4. Stop services

```bash
docker compose down
```

Use `docker-compose down` if your environment uses standalone Docker Compose.

To remove persisted metadata as well, delete the local SQLite metadata file or
the mounted data volume explicitly.