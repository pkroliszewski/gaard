from contextlib import asynccontextmanager
from importlib.resources import as_file, files
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gaard_api.api.v1.analysis import router as analysis_router
from gaard_api.api.v1.admin import get_current_admin, router as admin_router
from gaard_api.api.v1.dashboards import router as dashboards_router
from gaard_api.api.v1.prompts import router as prompts_router
from gaard_api.api.v1.query import router as query_router
from gaard_api.api.v1.schema import router as schema_router
from gaard_api.admin.models import AdminUser
from gaard_api.core.error_handlers import register_error_handlers
from gaard_api.extensions import get_api_registry
from gaard_api.license import license_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    license_service.start()
    try:
        yield
    finally:
        license_service.stop()


app = FastAPI(
    title="GAARD API",
    version="0.2.5",
    description="Self-hosted AI SQL Gateway for governed natural-language access to relational data.",
    lifespan=lifespan,
)

register_error_handlers(app)

with as_file(files("gaard_api").joinpath("admin-web")) as _path:
    ADMIN_WEB_DIR: Path = Path(_path).absolute()

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


@app.get("/license/status")
def license_status(user: AdminUser = Depends(get_current_admin)) -> dict[str, object]:
    return license_service.status()


app.include_router(query_router, prefix="/api/v1", tags=["query"])
app.include_router(analysis_router, prefix="/api/v1", tags=["analysis"])
app.include_router(dashboards_router, prefix="/api/v1", tags=["dashboards"])
app.include_router(schema_router, prefix="/api/v1", tags=["schema"])
app.include_router(prompts_router, prefix="/api/v1", tags=["prompts"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])

get_api_registry().apply_to(app)


@app.get("/admin", include_in_schema=False)
def admin_index() -> FileResponse:
    return FileResponse(ADMIN_WEB_DIR / "index.html")


@app.get("/admin/{path:path}", include_in_schema=False)
def admin_spa(path: str) -> FileResponse:
    return FileResponse(ADMIN_WEB_DIR / "index.html")
