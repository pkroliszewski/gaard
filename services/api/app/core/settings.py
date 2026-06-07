from dataclasses import dataclass, field
import json
import os
from typing import Any


def env_value(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int_value(name: str, default: int) -> int:
    value = os.environ.get(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def env_dict_value(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.environ.get(name)

    if value is None:
        return dict(default)

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return dict(default)

    return parsed if isinstance(parsed, dict) else dict(default)


@dataclass
class Settings:
    gaard_metadata_database_url: str = "sqlite:///./metadata.db"
    gaard_datasource_url: str = field(
        default_factory=lambda: env_value(
            "GAARD_DATASOURCE_URL",
            "sqlite:///./examples/medical-poc/demo.db",
        )
    )
    gaard_query_max_rows: int = field(
        default_factory=lambda: env_int_value("GAARD_QUERY_MAX_ROWS", 100)
    )
    gaard_query_timeout_seconds: int = field(
        default_factory=lambda: env_int_value("GAARD_QUERY_TIMEOUT_SECONDS", 30)
    )

    gaard_schema_cache_ttl_seconds: int = field(
        default_factory=lambda: env_int_value("GAARD_SCHEMA_CACHE_TTL_SECONDS", 300)
    )
    gaard_audit_retention_days: int = field(
        default_factory=lambda: env_int_value("GAARD_AUDIT_RETENTION_DAYS", 90)
    )

    gaard_intent_classification_mode: str = field(
        default_factory=lambda: env_value("GAARD_INTENT_CLASSIFICATION_MODE", "auto")
    )
    gaard_sql_generation_mode: str = field(
        default_factory=lambda: env_value("GAARD_SQL_GENERATION_MODE", "llm")
    )
    gaard_result_interpretation_mode: str = field(
        default_factory=lambda: env_value("GAARD_RESULT_INTERPRETATION_MODE", "llm")
    )
    gaard_output_classification_mode: str = field(
        default_factory=lambda: env_value("GAARD_OUTPUT_CLASSIFICATION_MODE", "auto")
    )
    gaard_investigation_mode: str = field(
        default_factory=lambda: env_value("GAARD_INVESTIGATION_MODE", "llm")
    )
    gaard_investigation_ambiguity_mode: str = field(
        default_factory=lambda: env_value("GAARD_INVESTIGATION_AMBIGUITY_MODE", "clarify")
    )

    gaard_llm_provider: str = field(
        default_factory=lambda: env_value("GAARD_LLM_PROVIDER", "openai-compatible")
    )
    gaard_llm_base_url: str = field(
        default_factory=lambda: env_value("GAARD_LLM_BASE_URL", "https://api.openai.com/v1")
    )
    gaard_llm_api_key: str = field(
        default_factory=lambda: env_value("GAARD_LLM_API_KEY", "change-me")
    )
    gaard_llm_model: str = field(
        default_factory=lambda: env_value("GAARD_LLM_MODEL", "gpt-4.1-mini")
    )
    gaard_llm_extra_body: dict[str, Any] = field(
        default_factory=lambda: env_dict_value("GAARD_LLM_EXTRA_BODY", {})
    )
    gaard_llm_timeout_seconds: int = field(
        default_factory=lambda: env_int_value("GAARD_LLM_TIMEOUT_SECONDS", 60)
    )

    gaard_sql_dialect: str = field(
        default_factory=lambda: env_value("GAARD_SQL_DIALECT", "sqlite")
    )


settings = Settings()
