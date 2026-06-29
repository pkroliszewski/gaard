from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gaard_core.query_pipeline.models import OutputClassification


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(255), index=True)
    resource_type: Mapped[str] = mapped_column(String(255), index=True)
    resource_id: Mapped[str] = mapped_column(String(255), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class DataQueryAuditType(StrEnum):
    INFO = "info"
    SQL_ERROR = "sql_error"
    ACCESS_ERROR = "access_error"


class DataQueryAuditLog(Base):
    __tablename__ = "data_query_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    type: Mapped[DataQueryAuditType] = mapped_column(
        SAEnum(
            DataQueryAuditType,
            values_callable=lambda items: [item.value for item in items],
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            length=50,
            name="data_query_audit_type",
        ),
        default=DataQueryAuditType.INFO,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    datasource_id: Mapped[str] = mapped_column(String(255), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text)
    output_classification: Mapped[OutputClassification] = mapped_column(
        SAEnum(
            OutputClassification,
            values_callable=lambda items: [item.value for item in items],
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            length=50,
            name="output_classification",
        ),
        default=OutputClassification.UNKNOWN,
        index=True,
    )
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("prompt_key", name="uq_prompt_templates_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_key: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    user_prompt_template: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class DatasourceConnector(Base):
    __tablename__ = "datasource_connectors"
    __table_args__ = (UniqueConstraint("connector_key", name="uq_datasource_connectors_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_key: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))
    database_type: Mapped[str] = mapped_column(String(50))
    database_url: Mapped[str] = mapped_column(Text)
    sql_dialect: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class DatasourceSchemaCache(Base):
    __tablename__ = "datasource_schema_caches"

    connector_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_json: Mapped[str] = mapped_column(Text)
    table_settings_json: Mapped[str] = mapped_column(Text, default="{}")
    formatted_schema: Mapped[str] = mapped_column(Text, default="")
    introspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class OverviewWidget(Base):
    __tablename__ = "overview_widgets"
    __table_args__ = (UniqueConstraint("widget_key", name="uq_overview_widgets_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    widget_key: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[str] = mapped_column(String(255))
    widget_type: Mapped[str] = mapped_column(String(50))
    datasource_key: Mapped[str] = mapped_column(String(255), default="metadata-db")
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text, default="")
    result_mode: Mapped[str] = mapped_column(String(50), default="data")
    position: Mapped[int] = mapped_column(Integer, default=100)
    grid_width: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class BusinessLogicSuggestion(Base):
    __tablename__ = "business_logic_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_id: Mapped[int] = mapped_column(Integer, index=True)
    source_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), index=True, default="pending")
    safety: Mapped[str] = mapped_column(String(50), index=True, default="review")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    error_category: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    rule_text: Mapped[str] = mapped_column(Text)
    terms_json: Mapped[str] = mapped_column(Text, default="[]")
    join_hints_json: Mapped[str] = mapped_column(Text, default="[]")
    failed_identifier: Mapped[str] = mapped_column(String(255), default="")
    repaired_identifier: Mapped[str] = mapped_column(String(255), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class BusinessKnowledgeClaim(Base):
    __tablename__ = "business_knowledge_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_id: Mapped[int] = mapped_column(Integer, index=True)
    knowledge_type: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(50), index=True, default="candidate")
    claim_text: Mapped[str] = mapped_column(Text)
    subject_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(100), index=True, default="query_pipeline")
    request_id: Mapped[str] = mapped_column(String(255), index=True, default="")
    audit_reference: Mapped[str] = mapped_column(String(255), default="")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class AnalysisSessionRecord(Base):
    __tablename__ = "analysis_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), index=True, default="running")
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    datasource_id: Mapped[str] = mapped_column(String(255), index=True)
    question: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
