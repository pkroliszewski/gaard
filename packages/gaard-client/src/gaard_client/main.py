import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx2 as httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from importlib.resources import files


CLIENT_WEB_DIR = files("gaard_client").joinpath("client-web")
DEFAULT_BACKEND_URL = "http://localhost:8000"

app = FastAPI(
    title="GAARD Client",
    version="0.2.1",
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
async def query_backend(request: ClientQueryRequest) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    query_url = f"{backend_url}/api/v1/query"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                query_url,
                json={
                    "question": request.question,
                    "user_id": "client",
                    "mode": request.mode,
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

    return response.json()


@app.post("/api/widgets/from-query")
async def create_widget_from_query(request: ClientWidgetFromQueryRequest) -> dict[str, Any]:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    widget_url = f"{backend_url}/api/v1/admin/overview/widgets/from-query"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                widget_url,
                json={
                    "label": request.label,
                    "widget_type": request.widget_type,
                    "datasource_key": request.datasource_key,
                    "question": request.question,
                    "sql": request.sql,
                    "result_mode": request.result_mode,
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

    return response.json()


@app.post("/api/query/stream")
async def query_backend_stream(request: ClientQueryRequest) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    query_url = f"{backend_url}/api/v1/query/stream"

    async def stream_backend():
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    query_url,
                    json={
                        "question": request.question,
                        "user_id": "client",
                        "mode": request.mode,
                    },
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
async def analysis_backend_stream(request: ClientQueryRequest) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    analysis_url = f"{backend_url}/api/v1/analysis/stream"

    async def stream_backend():
        async for chunk in proxy_stream(
            url=analysis_url,
            payload={
                "question": request.question,
                "user_id": "client",
            },
        ):
            yield chunk

    return StreamingResponse(stream_backend(), media_type="application/x-ndjson")


@app.post("/api/analysis/{session_id}/messages/stream")
async def analysis_message_backend_stream(
    session_id: str,
    request: ClientAnalysisMessageRequest,
) -> StreamingResponse:
    backend_url = normalize_backend_url(request.backend_url or get_default_backend_url())
    analysis_url = f"{backend_url}/api/v1/analysis/{session_id}/messages/stream"

    async def stream_backend():
        async for chunk in proxy_stream(
            url=analysis_url,
            payload={
                "message": request.message,
            },
        ):
            yield chunk

    return StreamingResponse(stream_backend(), media_type="application/x-ndjson")


async def proxy_stream(url: str, payload: dict[str, Any]):
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=payload) as response:
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
