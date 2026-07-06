from gaard_api.core.settings import Settings


def test_settings_use_llm_defaults_and_ignore_metadata_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "GAARD_METADATA_DATABASE_URL",
        "postgresql+psycopg://gaard:gaard@localhost:5432/gaard",
    )

    settings = Settings()

    assert settings.gaard_metadata_database_url == "sqlite:///./metadata.db"
    assert settings.gaard_sql_generation_mode == "llm"
    assert settings.gaard_result_interpretation_mode == "llm"
    assert settings.gaard_intent_classification_mode == "auto"
    assert settings.gaard_package_directory == "extensions"


def test_settings_can_seed_runtime_defaults_from_process_env(monkeypatch) -> None:
    monkeypatch.setenv("GAARD_SQL_GENERATION_MODE", "mock")
    monkeypatch.setenv("GAARD_RESULT_INTERPRETATION_MODE", "mock")
    monkeypatch.setenv(
        "GAARD_LLM_EXTRA_BODY",
        '{"chat_template_kwargs":{"enable_thinking":false}}',
    )

    settings = Settings()

    assert settings.gaard_sql_generation_mode == "mock"
    assert settings.gaard_result_interpretation_mode == "mock"
    assert settings.gaard_llm_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
