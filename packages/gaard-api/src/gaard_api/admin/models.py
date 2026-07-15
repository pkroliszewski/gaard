from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
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
    __table_args__ = (
        UniqueConstraint("auth_provider", "username", name="uq_admin_users_auth_provider_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    auth_provider: Mapped[str] = mapped_column(String(255), index=True, default="local")
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
    username: Mapped[str] = mapped_column(String(255), index=True, default="")
    role: Mapped[str] = mapped_column(String(50), index=True, default="admin")
    auth_provider: Mapped[str] = mapped_column(String(255), index=True, default="local")
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
    llm_sql_language: Mapped[str] = mapped_column(String(50), default="")
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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), index=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    status: Mapped[str] = mapped_column(String(50), index=True, default="active")
    datasource_id: Mapped[str] = mapped_column(String(255), index=True, default="")
    datasource_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    title: Mapped[str] = mapped_column(String(255), default="")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(50), index=True, default="sql")
    status: Mapped[str] = mapped_column(String(50), index=True, default="completed")
    original_question: Mapped[str] = mapped_column(Text)
    standalone_question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    sql: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    data_query_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    context_decision: Mapped[str] = mapped_column(String(50), index=True, default="new_topic")
    context_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), index=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class DashboardUserState(Base):
    __tablename__ = "dashboard_user_states"

    owner_user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    active_dashboard_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class UserDatasourceSelection(Base):
    """Client datasource selections, scoped to one authenticated user."""

    __tablename__ = "user_datasource_selections"

    owner_user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    datasource_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class UserSavedMetric(Base):
    __tablename__ = "user_saved_metrics"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "widget_key", name="uq_user_saved_metrics_owner_widget"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), index=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    widget_key: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"
    __table_args__ = (
        UniqueConstraint("widget_id", name="uq_dashboard_widgets_widget_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    widget_id: Mapped[str] = mapped_column(String(64), index=True)
    dashboard_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), index=True)
    owner_username: Mapped[str] = mapped_column(String(255), index=True, default="")
    metric_widget_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    visualization_type: Mapped[str] = mapped_column(String(50), default="table")
    x: Mapped[int] = mapped_column(Integer, default=0)
    y: Mapped[int] = mapped_column(Integer, default=0)
    w: Mapped[int] = mapped_column(Integer, default=6)
    h: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


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
    grid_height: Mapped[int] = mapped_column(Integer, default=2)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    updated_by: Mapped[str] = mapped_column(String(255), default="system")


class WidgetTag(Base):
    """Permanent catalogue of every tag that has been used on a widget."""

    __tablename__ = "widget_tags"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OverviewWidgetTag(Base):
    __tablename__ = "overview_widget_tags"

    widget_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("overview_widgets.id", ondelete="CASCADE"), primary_key=True
    )
    tag_name: Mapped[str] = mapped_column(
        String(255), ForeignKey("widget_tags.name"), primary_key=True
    )


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
