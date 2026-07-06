from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from gaard_core.errors import GaardError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gaard_extract import service


SessionFactory = Callable[[], Session]
EXTRACT_JOBS_FEATURE = "extract_jobs"
EXTRACT_JOBS_LICENSE_MESSAGE = "Extract jobs require an active Enterprise license."


class LicenseHostService(Protocol):
    def require_feature(self, feature: str, detail: str | None = None) -> None:
        ...


class SourceModelRequest(BaseModel):
    main_table: str = Field(min_length=1)
    table_roles: dict[str, dict[str, str | None]] = Field(default_factory=dict)


class ChunkingConfigRequest(BaseModel):
    mode: str = Field(min_length=1)


class EmbeddingConfigRequest(BaseModel):
    enabled: bool
    provider: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    clear_api_key: bool = False
    model: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class LlmExtractingConfigRequest(BaseModel):
    blueprint_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    domain_description: str = ""
    case_grain_description: str = ""
    language: str = Field(default="pl", min_length=1)
    status: str = Field(default="draft", min_length=1)
    information_types: list[Any] = Field(default_factory=list)
    global_rules: list[Any] = Field(default_factory=list)
    review_policy: dict[str, Any] = Field(default_factory=dict)
    extraction_scope: dict[str, Any] = Field(default_factory=dict)
    json_schema: dict[str, Any] | None = None


class KeyDescriptionSuggestionRequest(BaseModel):
    key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    domain_description: str = ""
    case_grain_description: str = ""
    language: str = Field(default="pl", min_length=1)


class KeyFieldsSuggestionRequest(BaseModel):
    key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str = ""
    domain_description: str = ""
    case_grain_description: str = ""
    language: str = Field(default="pl", min_length=1)


def create_router(
    session_factory: SessionFactory,
    datasource_service: service.DatasourceHostService,
    license_service: LicenseHostService | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/datasources")
    def list_datasources() -> dict[str, Any]:
        return {"items": service.list_datasources(datasource_service)}

    @router.get("/datasources/{connector_id}/schema")
    def get_datasource_schema(connector_id: int) -> dict[str, Any]:
        try:
            schema = service.get_datasource_schema(datasource_service, connector_id)
        except (KeyError, ValueError) as exc:
            raise http_error(exc) from exc
        return {"item": schema}

    @router.get("/source-models")
    def list_source_models() -> dict[str, Any]:
        with session_factory() as session:
            return {"items": service.list_source_models(session)}

    @router.get("/source-models/{connector_id}")
    def get_source_model(connector_id: int) -> dict[str, Any]:
        with session_factory() as session:
            return {"item": service.get_source_model(session, connector_id)}

    @router.put("/source-models/{connector_id}")
    def upsert_source_model(connector_id: int, request: SourceModelRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.upsert_source_model(
                    session,
                    datasource_service,
                    datasource_connector_id=connector_id,
                    main_table=request.main_table,
                    table_roles=request.table_roles,
                    updated_by="admin",
                )
            except (KeyError, ValueError) as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    @router.get("/chunking-config")
    def get_chunking_config() -> dict[str, Any]:
        with session_factory() as session:
            return {"item": service.get_chunking_config(session)}

    @router.put("/chunking-config")
    def upsert_chunking_config(request: ChunkingConfigRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.upsert_chunking_config(
                    session,
                    mode=request.mode,
                    updated_by="admin",
                )
            except ValueError as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    @router.get("/embedding-config")
    def get_embedding_config() -> dict[str, Any]:
        with session_factory() as session:
            return {"item": service.get_embedding_config(session)}

    @router.put("/embedding-config")
    def upsert_embedding_config(request: EmbeddingConfigRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.upsert_embedding_config(
                    session,
                    enabled=request.enabled,
                    provider=request.provider,
                    base_url=request.base_url,
                    api_key=request.api_key,
                    clear_api_key=request.clear_api_key,
                    model=request.model,
                    timeout_seconds=request.timeout_seconds,
                    extra_body=request.extra_body,
                    updated_by="admin",
                )
            except ValueError as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    @router.post("/embedding-config/test")
    def test_embedding_config(request: EmbeddingConfigRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                return {
                    "item": service.test_embedding_config(
                        session,
                        enabled=request.enabled,
                        provider=request.provider,
                        base_url=request.base_url,
                        api_key=request.api_key,
                        clear_api_key=request.clear_api_key,
                        model=request.model,
                        timeout_seconds=request.timeout_seconds,
                        extra_body=request.extra_body,
                    )
                }
            except ValueError as exc:
                raise http_error(exc) from exc

    @router.get("/llm-extracting-config")
    def get_llm_extracting_config() -> dict[str, Any]:
        with session_factory() as session:
            return {"item": service.get_llm_extracting_config(session)}

    @router.put("/llm-extracting-config")
    def upsert_llm_extracting_config(request: LlmExtractingConfigRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.upsert_llm_extracting_config(
                    session,
                    blueprint_key=request.blueprint_key,
                    name=request.name,
                    description=request.description,
                    domain_description=request.domain_description,
                    case_grain_description=request.case_grain_description,
                    language=request.language,
                    status=request.status,
                    information_types=request.information_types,
                    global_rules=request.global_rules,
                    review_policy=request.review_policy,
                    extraction_scope=request.extraction_scope,
                    json_schema=request.json_schema,
                    updated_by="admin",
                )
            except ValueError as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    @router.post("/llm-extracting-config/key-description-suggestion")
    def suggest_key_description(request: KeyDescriptionSuggestionRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.suggest_key_description(
                    session,
                    key=request.key,
                    kind=request.kind,
                    domain_description=request.domain_description,
                    case_grain_description=request.case_grain_description,
                    language=request.language,
                )
            except ValueError as exc:
                raise http_error(exc) from exc
            return {"item": item}

    @router.post("/llm-extracting-config/key-fields-suggestion")
    def suggest_key_fields(request: KeyFieldsSuggestionRequest) -> dict[str, Any]:
        with session_factory() as session:
            try:
                item = service.suggest_key_fields(
                    session,
                    key=request.key,
                    kind=request.kind,
                    description=request.description,
                    domain_description=request.domain_description,
                    case_grain_description=request.case_grain_description,
                    language=request.language,
                )
            except ValueError as exc:
                raise http_error(exc) from exc
            return {"item": item}

    @router.get("/jobs")
    def list_jobs() -> dict[str, Any]:
        with session_factory() as session:
            return {"items": service.list_jobs(session)}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        with session_factory() as session:
            try:
                return {"item": service.get_job(session, job_id)}
            except KeyError as exc:
                raise http_error(exc) from exc

    @router.get("/jobs/{job_id}/config")
    def get_job_config(job_id: str) -> dict[str, Any]:
        with session_factory() as session:
            try:
                return {"item": service.get_job_config(session, job_id)}
            except KeyError as exc:
                raise http_error(exc) from exc

    @router.post("/jobs")
    def create_job() -> dict[str, Any]:
        require_extract_jobs_license(license_service)
        with session_factory() as session:
            try:
                item = service.create_job_from_current_config(session, updated_by="admin")
            except (KeyError, ValueError) as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    @router.post("/jobs/{job_id}/refresh")
    def refresh_job(job_id: str) -> dict[str, Any]:
        require_extract_jobs_license(license_service)
        with session_factory() as session:
            try:
                item = service.refresh_job_from_history(
                    session,
                    source_job_id=job_id,
                    updated_by="admin",
                )
            except (KeyError, ValueError) as exc:
                raise http_error(exc) from exc
            session.commit()
            return {"item": item}

    return router


def http_error(exc: Exception) -> HTTPException:
    status_code = 404 if isinstance(exc, KeyError) else 400
    return HTTPException(status_code=status_code, detail=str(exc))


def require_extract_jobs_license(license_service: LicenseHostService | None) -> None:
    require_feature = getattr(license_service, "require_feature", None)
    if require_feature is None:
        raise HTTPException(status_code=403, detail=EXTRACT_JOBS_LICENSE_MESSAGE)

    try:
        require_feature(EXTRACT_JOBS_FEATURE, EXTRACT_JOBS_LICENSE_MESSAGE)
    except GaardError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        ) from exc
