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
    version="0.2.2",
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


class ClientLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
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
    result_mode: str = "data"
    backend_url: str | None = None


class ClientDatasourceStateRequest(BaseModel):
    active: bool
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
    payload = {
        "question": request.question,
        "user_id": "client",
        "mode": request.mode,
    }
    if request.conversation_id:
        payload["conversation_id"] = request.conversation_id
    if request.context_mode != "auto":
        payload["context_mode"] = request.context_mode
    return payload


def analysis_payload(request: ClientQueryRequest) -> dict[str, Any]:
    payload = {
        "question": request.question,
        "user_id": "client",
    }
    if request.conversation_id:
        payload["conversation_id"] = request.conversation_id
    if request.context_mode != "auto":
        payload["context_mode"] = request.context_mode
    return payload


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
    datasources_url = f"{backend_url}/api/v1/admin/datasources"

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


@app.post("/api/datasources/excel")
async def upload_excel_datasource(
    file: UploadFile = File(...),
    active: bool = False,
    backend_url: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Wybierz plik w formacie .xlsx.")

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
