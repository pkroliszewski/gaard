from fastapi import APIRouter
from pydantic import BaseModel, Field

from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.prompt_compiler.models import SqlGenerationPromptRequest
from gaard_core.prompt_compiler.sql_generation_prompt import SqlGenerationPromptCompiler
from gaard_core.schema.context import SchemaContextService

from gaard_api.admin.prompt_runtime import get_sql_generation_prompt_compiler
from gaard_api.admin.services import get_datasource_schema_context_safe, get_query_runtime_config_safe
from gaard_api.api.v1.schema import get_schema_cache_key
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings
from gaard_api.extensions import get_connector_registry

router = APIRouter()


class CompileSqlGenerationPromptApiRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("/prompts/sql-generation", response_model=CompiledPrompt)
def compile_sql_generation_prompt(
    request: CompileSqlGenerationPromptApiRequest,
) -> CompiledPrompt:
    datasource_context = get_datasource_schema_context_safe()

    if datasource_context is not None:
        connector, schema_cache = datasource_context
        formatted_schema = schema_cache.formatted_schema
        sql_dialect = connector.sql_dialect
    else:
        sql_dialect = settings.gaard_sql_dialect
        introspector = get_connector_registry().detect_from_database_url(
            settings.gaard_datasource_url
        ).introspector_factory(settings.gaard_datasource_url)
        service = SchemaContextService(
            introspector=introspector,
            cache=schema_context_cache,
        )
        context = service.get_schema_context(get_schema_cache_key())
        formatted_schema = context.formatted_schema

    compiler = get_sql_generation_prompt_compiler() or SqlGenerationPromptCompiler()
    runtime_config = get_query_runtime_config_safe()

    return compiler.compile(
        SqlGenerationPromptRequest(
            question=request.question,
            formatted_schema=formatted_schema,
            dialect=sql_dialect,
            max_rows=runtime_config.query_max_rows,
        )
    )
