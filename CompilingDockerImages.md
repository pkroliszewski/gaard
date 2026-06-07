# Compiling Docker Images for Transfer

This guide describes how to build GAARD container images on one machine, copy
them to another machine, and run them with Podman.

The repository already contains:

- `docker-compose.yml`, which defines the API service
- `services/api/Dockerfile`, which builds the GAARD API image

## 1. Prepare the demo datasource

Generate the SQLite demo database before building the API image. The current API
image copies the `examples/` directory at build time, so the generated database
must already exist.

```bash
python examples/medical-poc/create_demo_db.py
```

Runtime configuration is seeded into the SQLite metadata database on first API
start and then managed from `/admin`.

## 2. Build the API image

From the repository root:

```bash
docker build \
  -t gaard-api:local \
  -f services/api/Dockerfile \
  .
```

## 3. Save images to an archive

```bash
docker save -o gaard-images.tar gaard-api:local
```

The archive contains the API image.

## 4. Copy files to the target machine

Copy the image archive and compose file if you want to keep it for reference:

```bash
scp gaard-images.tar docker-compose.yml user@host:/opt/gaard/
```

## 5. Load images with Podman

On the target machine:

```bash
cd /opt/gaard
podman load -i gaard-images.tar
```

## 6. Run with Podman

Start the GAARD API:

```bash
podman run -d \
  --name gaard-api \
  -p 8000:8000 \
  gaard-api:local
```

The API should be available at:

```text
http://localhost:8000
```

## 7. Check health

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

## Podman Compose option

If the target machine has `podman-compose`, you can adapt `docker-compose.yml`
to use the transferred image instead of rebuilding from source. Replace the API
service `build` block with:

```yaml
image: gaard-api:local
```

Then run:

```bash
podman-compose up
```

## Architecture note

Build images for the CPU architecture used by the target machine. For example,
if you build on Apple Silicon but deploy to an `amd64` Linux server, build the
API image for `linux/amd64`:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t gaard-api:local \
  -f services/api/Dockerfile \
  --load \
  .
```

Then save the images as described above.
