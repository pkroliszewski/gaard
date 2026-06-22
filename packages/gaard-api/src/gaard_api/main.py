from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gaard_api.api.v1.admin import router as admin_router
from gaard_api.api.v1.prompts import router as prompts_router
from gaard_api.api.v1.query import router as query_router
from gaard_api.api.v1.schema import router as schema_router
from gaard_api.core.error_handlers import register_error_handlers
from importlib.resources import files

app = FastAPI(
    title="GAARD API",
    version="0.1.0",
    description="Self-hosted AI SQL Gateway for governed natural-language access to relational data.",
)

register_error_handlers(app)

ADMIN_WEB_DIR = files("gaard_api").joinpath("admin-web")

if ADMIN_WEB_DIR.exists():
    app.mount(
        "/admin/assets",
        StaticFiles(directory=ADMIN_WEB_DIR / "assets"),
        name="admin-assets",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(query_router, prefix="/api/v1", tags=["query"])
app.include_router(schema_router, prefix="/api/v1", tags=["schema"])
app.include_router(prompts_router, prefix="/api/v1", tags=["prompts"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])


@app.get("/admin", include_in_schema=False)
def admin_index() -> FileResponse:
    return FileResponse(ADMIN_WEB_DIR / "index.html")


@app.get("/admin/{path:path}", include_in_schema=False)
def admin_spa(path: str) -> FileResponse:
    return FileResponse(ADMIN_WEB_DIR / "index.html")
