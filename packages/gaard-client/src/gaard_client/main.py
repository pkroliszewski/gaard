import json
import os

from collections.abc import AsyncIterator
from importlib.resources import as_file, files
from pathlib import Path

from typing import Any, cast
from urllib.parse import urlparse

import httpx2 as httpx
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


with as_file(files("gaard_client").joinpath("client-web")) as _path:
    CLIENT_WEB_DIR: Path = Path(_path).absolute()

DEFAULT_BACKEND_URL = "http://localhost:8000"


app = FastAPI(
    title="GAARD Client",
    version="0.2.8",
    description="Community client for asking governed natural-language questions.",
)

if CLIENT_WEB_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=CLIENT_WEB_DIR / "assets"),
        name="client-assets",
    )


class ClientQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    backend_url: str | None = None
    mode: str = "sql"
    conversation_id: str | None = None
    context_mode: str = "auto"
    datasource_ids: list[str] = Field(default_factory=list)


class ClientLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    backend_url: str | None = None


class ClientChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    backend_url: str | None = None


class ClientAnalysisMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    backend_url: str | None = None


class ClientWidgetFromQueryRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    widget_type: str = "table"
    datasource_key: str = "default"
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    result_mode: str = "data"
    backend_url: str | None = None


class ClientWidgetTitleSuggestionRequest(BaseModel):
    question: str = Field(min_length=1)
    sql: str | None = None
    backend_url: str | None = None


class ClientQueryExplanationRequest(BaseModel):
    question: str = Field(min_length=1)
    sql: str = ""
    answer: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    inference_metadata: dict[str, Any] = Field(default_factory=dict)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    business_logic: str = ""
    datasource_id: str = ""
    datasource_ids: list[str] = Field(default_factory=list)
    backend_url: str | None = None


class ClientDatasourceStateRequest(BaseModel):
    active: bool
    backend_url: str | None = None


class ClientDatasourceSelectionRequest(BaseModel):
    datasource_ids: list[str] = Field(default_factory=list)
    backend_url: str | None = None


class ClientDashboardCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)
    backend_url: str | None = None


class ClientDashboardUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)
    backend_url: str | None = None


class ClientActiveDashboardRequest(BaseModel):
    dashboard_id: str = Field(min_length=1, max_length=64)
    backend_url: str | None = None


class ClientDashboardWidgetCreateRequest(BaseModel):
    metric_widget_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    visualization_type: str = Field(min_length=1, max_length=50)
    backend_url: str | None = None


class ClientSavedMetricUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    backend_url: str | None = None


class ClientDashboardWidgetLayoutItem(BaseModel):
    widget_id: str = Field(min_length=1, max_length=64)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=30)


class ClientDashboardWidgetLayoutRequest(BaseModel):
    items: list[ClientDashboardWidgetLayoutItem] = Field(default_factory=list)
    backend_url: str | None = None


def get_default_backend_url() -> str:
    return os.getenv("GAARD_CLIENT_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def normalize_backend_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Backend URL must be an absolute http or https URL.",
        )

    return normalized


def backend_auth_headers(authorization: str | None) -> dict[str, str]:
    return {"Authorization": authorization} if authorization else {}


def backend_request_kwargs(
    authorization: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"json": payload}
    headers = backend_auth_headers(authorization)
    if headers:
        kwargs["headers"] = headers
    return kwargs


def query_payload(request: ClientQueryRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": request.question,
        "user_id": "client",
        "mode": request.mode,
    }
    if request.conversation_id:
        payload["conversation_id"] = request.conversation_id
    if request.context_mode != "auto":
        payload["context_mode"] = request.context_mode
    if request.datasource_ids:
        payload["datasource_ids"] = request.datasource_ids
    return payload


def analysis_payload(request: ClientQueryRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": request.question,
        "user_id": "client",
    }
    if request.conversation_id:
        payload["conversation_id"] = request.conversation_id
    if request.context_mode != "auto":
        payload["context_mode"] = request.context_mode
    if request.datasource_ids:
        payload["datasource_ids"] = request.datasource_ids
    return payload


def query_explanation_payload(request: ClientQueryExplanationRequest) -> dict[str, Any]:
    return {
        "question": request.question,
        "sql": request.sql,
        "answer": request.answer,
        "rows": request.rows,
        "columns": request.columns,
        "metadata": request.metadata,
        "inference_metadata": request.inference_metadata,
        "prompt_metadata": request.prompt_metadata,
        "business_logic": request.business_logic,
        "datasource_id": request.datasource_id,
        "datasource_ids": request.datasource_ids,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config.js", include_in_schema=False)
def config_js() -> Response:
    payload = {
        "backendUrl": get_default_backend_url(),
    }
    content = f"window.GAARD_CLIENT_CONFIG = {json.dumps(payload)};"

    return Response(content=content, media_type="application/javascript")


@app.post("/api/query")
async def query_backend(
    request: ClientQueryRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    query_url = f"{backend_url}/api/v1/query"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                query_url,
                **backend_request_kwargs(authorization, query_payload(request)),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/query/explain")
async def explain_query_backend(
    request: ClientQueryExplanationRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    explain_url = f"{backend_url}/api/v1/query/explain"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                explain_url,
                **backend_request_kwargs(authorization, query_explanation_payload(request)),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/auth/login")
async def login_backend(request: ClientLoginRequest) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    login_url = f"{backend_url}/api/v1/admin/auth/login"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                login_url,
                json={
                    "username": request.username,
                    "password": request.password,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/auth/change-password")
async def change_password_backend(
    request: ClientChangePasswordRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    change_password_url = f"{backend_url}/api/v1/admin/auth/change-password"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                change_password_url,
                **backend_request_kwargs(authorization, {
                    "current_password": request.current_password,
                    "new_password": request.new_password,
                }),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/auth/logout")
async def logout_backend(
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    resolved_backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    logout_url = f"{resolved_backend_url}/api/v1/admin/auth/logout"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                logout_url,
                headers={"Authorization": authorization} if authorization else {},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.get("/api/auth/me")
async def get_current_user_backend(
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    resolved_backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    me_url = f"{resolved_backend_url}/api/v1/admin/me"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                me_url,
                headers={"Authorization": authorization} if authorization else {},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/widgets/title-suggestion")
async def suggest_widget_title(
    request: ClientWidgetTitleSuggestionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    suggestion_url = f"{backend_url}/api/v1/admin/overview/widgets/title-suggestion"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                suggestion_url,
                **backend_request_kwargs(authorization, {
                    "question": request.question,
                    "sql": request.sql,
                }),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/widgets/from-query")
async def create_widget_from_query(
    request: ClientWidgetFromQueryRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    widget_url = f"{backend_url}/api/v1/admin/overview/widgets/from-query"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                widget_url,
                **backend_request_kwargs(authorization, {
                    "label": request.label,
                    "widget_type": request.widget_type,
                    "datasource_key": request.datasource_key,
                    "question": request.question,
                    "sql": request.sql,
                    "rows": request.rows,
                    "result_mode": request.result_mode,
                }),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.get("/api/datasources")
async def list_datasources(
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    datasources_url = f"{backend_url}/api/v1/admin/datasources?available_only=true"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                datasources_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.api_route("/api/datasources/selection", methods=["PUT", "POST"])
async def update_datasource_selection(
    request: ClientDatasourceSelectionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    selection_url = f"{backend_url}/api/v1/admin/datasources/selection"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                selection_url,
                **backend_request_kwargs(
                    authorization, {"datasource_ids": request.datasource_ids}
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Backend request failed: {exc}") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/datasources/excel")
async def upload_excel_datasource(
    file: UploadFile = File(...),
    active: bool = False,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Choose an .xlsx file.")

    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    upload_url = f"{backend_url}/api/v1/admin/datasources/excel-upload"
    content = await file.read()

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                upload_url,
                headers=backend_auth_headers(authorization),
                params={"active": active},
                files={
                    "file": (
                        file.filename,
                        content,
                        file.content_type
                        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/datasources/{connector_id}/state")
async def update_datasource_state(
    connector_id: int,
    request: ClientDatasourceStateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    state_url = f"{backend_url}/api/v1/admin/datasources/{connector_id}/state"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                state_url,
                **backend_request_kwargs(authorization, {"active": request.active}),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.get("/api/dashboards")
async def list_dashboards(
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    dashboards_url = f"{backend_url}/api/v1/dashboards"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                dashboards_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/dashboards")
async def create_dashboard(
    request: ClientDashboardCreateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    dashboards_url = f"{backend_url}/api/v1/dashboards"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                dashboards_url,
                **backend_request_kwargs(
                    authorization,
                    {
                        "name": request.name,
                        "description": request.description,
                    },
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.put("/api/dashboards/active")
async def set_active_dashboard(
    request: ClientActiveDashboardRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    dashboard_url = f"{backend_url}/api/v1/dashboards/active"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                dashboard_url,
                **backend_request_kwargs(
                    authorization,
                    {
                        "dashboard_id": request.dashboard_id,
                    },
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.put("/api/dashboards/{dashboard_id}")
async def update_dashboard(
    dashboard_id: str,
    request: ClientDashboardUpdateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    dashboard_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                dashboard_url,
                **backend_request_kwargs(
                    authorization,
                    {
                        "name": request.name,
                        "description": request.description,
                    },
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.get("/api/dashboard-metrics")
async def list_dashboard_metrics(
    backend_url: str | None = None,
    include_result: bool = True,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    metrics_url = (
        f"{backend_url}/api/v1/dashboards/metrics"
        f"?include_result={'true' if include_result else 'false'}"
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(
                metrics_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.patch("/api/dashboard-metrics/{widget_key}")
async def update_dashboard_metric(
    widget_key: str,
    request: ClientSavedMetricUpdateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    metric_url = f"{backend_url}/api/v1/dashboards/metrics/{widget_key}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.patch(
                metric_url,
                **backend_request_kwargs(authorization, {"label": request.label}),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.delete("/api/dashboard-metrics/{widget_key}")
async def delete_dashboard_metric(
    widget_key: str,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    metric_url = f"{backend_url}/api/v1/dashboards/metrics/{widget_key}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.delete(
                metric_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.get("/api/dashboards/{dashboard_id}/widgets")
async def list_dashboard_widgets(
    dashboard_id: str,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    widgets_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}/widgets"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(
                widgets_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/dashboards/{dashboard_id}/widgets")
async def add_dashboard_widget(
    dashboard_id: str,
    request: ClientDashboardWidgetCreateRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    widgets_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}/widgets"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                widgets_url,
                **backend_request_kwargs(
                    authorization,
                    {
                        "metric_widget_key": request.metric_widget_key,
                        "title": request.title,
                        "visualization_type": request.visualization_type,
                    },
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.patch("/api/dashboards/{dashboard_id}/widgets/layout")
async def update_dashboard_widget_layout(
    dashboard_id: str,
    request: ClientDashboardWidgetLayoutRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    layout_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}/widgets/layout"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                layout_url,
                **backend_request_kwargs(
                    authorization,
                    {
                        "items": [
                            item.model_dump()
                            for item in request.items
                        ],
                    },
                ),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.delete("/api/dashboards/{dashboard_id}/widgets/{widget_id}")
async def delete_dashboard_widget(
    dashboard_id: str,
    widget_id: str,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    widget_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                widget_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.delete("/api/dashboards/{dashboard_id}")
async def delete_dashboard(
    dashboard_id: str,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    backend_url = normalize_backend_url(backend_url or get_default_backend_url())
    dashboard_url = f"{backend_url}/api/v1/dashboards/{dashboard_id}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                dashboard_url,
                headers=backend_auth_headers(authorization),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text,
        )

    return cast(dict[str, Any], response.json())


@app.post("/api/query/stream")
async def query_backend_stream(
    request: ClientQueryRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    query_url = f"{backend_url}/api/v1/query/stream"

    async def stream_backend() -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    query_url,
                    **backend_request_kwargs(authorization, query_payload(request)),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body.decode("utf-8", errors="replace")
                        yield json.dumps(
                            {
                                "error": {
                                    "code": "BACKEND_REQUEST_FAILED",
                                    "message": detail,
                                }
                            },
                            ensure_ascii=False,
                        ) + "\n"
                        return

                    async for chunk in response.aiter_text():
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            yield json.dumps(
                {
                    "error": {
                        "code": "BACKEND_REQUEST_FAILED",
                        "message": f"Backend request failed: {exc}",
                    }
                },
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(stream_backend(), media_type="application/x-ndjson")


@app.post("/api/analysis/stream")
async def analysis_backend_stream(
    request: ClientQueryRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    analysis_url = f"{backend_url}/api/v1/analysis/stream"

    async def stream_backend() -> AsyncIterator[str]:
        async for chunk in proxy_stream(
            url=analysis_url,
            payload=analysis_payload(request),
            authorization=authorization,
        ):
            yield chunk

    return StreamingResponse(stream_backend(), media_type="application/x-ndjson")


@app.post("/api/analysis/{session_id}/messages/stream")
async def analysis_message_backend_stream(
    session_id: str,
    request: ClientAnalysisMessageRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    analysis_url = f"{backend_url}/api/v1/analysis/{session_id}/messages/stream"

    async def stream_backend() -> AsyncIterator[str]:
        async for chunk in proxy_stream(
            url=analysis_url,
            payload={
                "message": request.message,
            },
            authorization=authorization,
        ):
            yield chunk

    return StreamingResponse(stream_backend(), media_type="application/x-ndjson")


async def proxy_stream(
    url: str,
    payload: dict[str, Any],
    authorization: str | None,
) -> AsyncIterator[str]:
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                url,
                **backend_request_kwargs(authorization, payload),
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace")
                    yield json.dumps(
                        {
                            "error": {
                                "code": "BACKEND_REQUEST_FAILED",
                                "message": detail,
                            }
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    return

                async for chunk in response.aiter_text():
                    if chunk:
                        yield chunk
    except httpx.HTTPError as exc:
        yield json.dumps(
            {
                "error": {
                    "code": "BACKEND_REQUEST_FAILED",
                    "message": f"Backend request failed: {exc}",
                }
            },
            ensure_ascii=False,
        ) + "\n"


@app.get("/", include_in_schema=False)
def client_index() -> FileResponse:
    return FileResponse(CLIENT_WEB_DIR / "index.html")


@app.get("/{path:path}", include_in_schema=False)
def client_spa(path: str) -> FileResponse:
    return FileResponse(CLIENT_WEB_DIR / "index.html")
