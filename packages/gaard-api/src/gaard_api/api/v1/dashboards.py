from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from gaard_api.admin.database import get_session
from gaard_api.admin.models import (
    Dashboard,
    DashboardUserState,
    DashboardWidget,
    OverviewWidget,
    OverviewWidgetTag,
    UserSavedMetric,
)
from gaard_api.api.v1.admin import (
    execute_overview_widget, 
    serialize_overview_widget_config,
    serialize_datetime,
    serialize_dashboard
)
from gaard_api.auth_dependencies import AuthenticatedSession, get_current_api_user

router = APIRouter()

DASHBOARD_VISUALIZATION_TYPES = {
    "number",
    "bar",
    "stacked_bar",
    "line",
    "multi_line",
    "pie",
    "area",
    "table",
}

DASHBOARD_WIDGET_DEFAULT_SIZES = {
    "number": (3, 2),
    "bar": (6, 4),
    "stacked_bar": (6, 4),
    "line": (6, 4),
    "multi_line": (6, 4),
    "pie": (4, 4),
    "area": (6, 4),
    "table": (8, 5),
}


class DashboardCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)


class DashboardUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)


class ActiveDashboardRequest(BaseModel):
    dashboard_id: str = Field(min_length=1, max_length=64)


class DashboardWidgetCreateRequest(BaseModel):
    metric_widget_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    visualization_type: str = Field(pattern=r"^(number|bar|stacked_bar|line|multi_line|pie|area|table)$")


class SavedMetricUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)


class DashboardWidgetLayoutItem(BaseModel):
    widget_id: str = Field(min_length=1, max_length=64)
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=30)


class DashboardWidgetLayoutRequest(BaseModel):
    items: list[DashboardWidgetLayoutItem] = Field(default_factory=list)


def dashboard_owner_user_id(principal: AuthenticatedSession) -> str:
    return str(principal.user.id)


def dashboard_owner_username(principal: AuthenticatedSession) -> str:
    return principal.session.username or principal.user.username


def saved_metric_tag_names(owner_username: str) -> tuple[str, str]:
    return ("public", owner_username)


def serialize_saved_metric(
    session: Session,
    metric: OverviewWidget,
    include_result: bool = True,
) -> dict[str, Any]:
    payload = serialize_overview_widget_config(session, metric)
    if include_result:
        payload["result"] = execute_overview_widget(session, metric)
    return payload


def serialize_dashboard_widget(
    session: Session,
    widget: DashboardWidget,
) -> dict[str, Any]:
    metric = session.scalar(
        select(OverviewWidget).where(OverviewWidget.widget_key == widget.metric_widget_key)
    )
    metric_payload = serialize_saved_metric(session, metric) if metric is not None else None
    return {
        "id": widget.widget_id,
        "dashboard_id": widget.dashboard_id,
        "metric_widget_key": widget.metric_widget_key,
        "title": widget.title,
        "visualization_type": widget.visualization_type,
        "layout": {
            "x": widget.x,
            "y": widget.y,
            "w": widget.w,
            "h": widget.h,
        },
        "metric": metric_payload,
        "result": metric_payload.get("result") if metric_payload else {
            "status": "error",
            "message": "Saved metric no longer exists.",
        },
        "created_at": serialize_datetime(widget.created_at),
        "updated_at": serialize_datetime(widget.updated_at),
    }


def get_dashboard_for_owner(
    session: Session,
    dashboard_id: str,
    owner_user_id: str,
) -> Dashboard | None:
    return session.scalar(
        select(Dashboard).where(
            Dashboard.dashboard_id == dashboard_id,
            Dashboard.owner_user_id == owner_user_id,
        )
    )


def get_or_create_dashboard_user_state(
    session: Session,
    principal: AuthenticatedSession,
) -> DashboardUserState:
    owner_user_id = dashboard_owner_user_id(principal)
    state = session.get(DashboardUserState, owner_user_id)
    if state is not None:
        state.owner_username = dashboard_owner_username(principal)
        return state

    state = DashboardUserState(
        owner_user_id=owner_user_id,
        owner_username=dashboard_owner_username(principal),
    )
    session.add(state)
    return state


def list_owner_dashboards(session: Session, owner_user_id: str) -> list[Dashboard]:
    return list(
        session.scalars(
            select(Dashboard)
            .where(Dashboard.owner_user_id == owner_user_id)
            .order_by(Dashboard.updated_at.desc(), Dashboard.id.desc())
        )
    )


def list_dashboard_widgets(
    session: Session,
    dashboard_id: str,
    owner_user_id: str,
) -> list[DashboardWidget]:
    return list(
        session.scalars(
            select(DashboardWidget)
            .where(
                DashboardWidget.dashboard_id == dashboard_id,
                DashboardWidget.owner_user_id == owner_user_id,
            )
            .order_by(DashboardWidget.y.asc(), DashboardWidget.x.asc(), DashboardWidget.id.asc())
        )
    )


def get_dashboard_widget_for_owner(
    session: Session,
    dashboard_id: str,
    widget_id: str,
    owner_user_id: str,
) -> DashboardWidget | None:
    return session.scalar(
        select(DashboardWidget).where(
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.widget_id == widget_id,
            DashboardWidget.owner_user_id == owner_user_id,
        )
    )


def get_saved_metric_for_owner(
    session: Session,
    widget_key: str,
    owner_username: str,
) -> OverviewWidget | None:
    public_tag, owner_tag = saved_metric_tag_names(owner_username)
    public_assignment = aliased(OverviewWidgetTag)
    owner_assignment = aliased(OverviewWidgetTag)
    return session.scalar(
        select(OverviewWidget)
        .join(public_assignment, public_assignment.widget_id == OverviewWidget.id)
        .join(owner_assignment, owner_assignment.widget_id == OverviewWidget.id)
        .where(
            OverviewWidget.widget_key == widget_key,
            public_assignment.tag_name == public_tag,
            owner_assignment.tag_name == owner_tag,
        )
    )


def list_saved_metrics_for_owner(
    session: Session,
    owner_username: str,
) -> list[OverviewWidget]:
    public_tag, owner_tag = saved_metric_tag_names(owner_username)
    public_assignment = aliased(OverviewWidgetTag)
    owner_assignment = aliased(OverviewWidgetTag)
    return list(
        session.scalars(
            select(OverviewWidget)
            .join(public_assignment, public_assignment.widget_id == OverviewWidget.id)
            .join(owner_assignment, owner_assignment.widget_id == OverviewWidget.id)
            .where(
                public_assignment.tag_name == public_tag,
                owner_assignment.tag_name == owner_tag,
            )
            .order_by(OverviewWidget.updated_at.desc(), OverviewWidget.id.desc())
        )
    )


def next_dashboard_widget_position(widgets: list[DashboardWidget]) -> int:
    if not widgets:
        return 0
    return max(widget.y + widget.h for widget in widgets)


def default_dashboard_widget_size(visualization_type: str) -> tuple[int, int]:
    return DASHBOARD_WIDGET_DEFAULT_SIZES.get(visualization_type, (6, 4))


def resolve_active_dashboard_id(
    session: Session,
    principal: AuthenticatedSession,
    dashboards: list[Dashboard],
) -> str:
    if not dashboards:
        state = get_or_create_dashboard_user_state(session, principal)
        state.active_dashboard_id = ""
        return ""

    owner_user_id = dashboard_owner_user_id(principal)
    state = get_or_create_dashboard_user_state(session, principal)
    if state.active_dashboard_id and any(
        dashboard.dashboard_id == state.active_dashboard_id for dashboard in dashboards
    ):
        return state.active_dashboard_id

    state.active_dashboard_id = dashboards[0].dashboard_id
    state.owner_user_id = owner_user_id
    state.owner_username = dashboard_owner_username(principal)
    return state.active_dashboard_id


def serialize_dashboard_collection(
    session: Session,
    principal: AuthenticatedSession,
) -> dict[str, Any]:
    dashboards = list_owner_dashboards(session, dashboard_owner_user_id(principal))
    active_dashboard_id = resolve_active_dashboard_id(session, principal, dashboards)
    active_dashboard = next(
        (
            dashboard
            for dashboard in dashboards
            if dashboard.dashboard_id == active_dashboard_id
        ),
        None,
    )

    return {
        "items": [serialize_dashboard(dashboard) for dashboard in dashboards],
        "active_dashboard_id": active_dashboard_id,
        "active_dashboard": serialize_dashboard(active_dashboard)
        if active_dashboard is not None
        else None,
        "viewer": dashboard_owner_username(principal),
    }


@router.get("/dashboards")
def list_dashboards(
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload = serialize_dashboard_collection(session, principal)
    session.commit()

    return payload


@router.get("/dashboards/metrics")
def list_saved_dashboard_metrics(
    include_result: bool = True,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    metrics = list_saved_metrics_for_owner(session, dashboard_owner_username(principal))
    return {
        "items": [
            serialize_saved_metric(session, metric, include_result=include_result)
            for metric in metrics
        ],
        "viewer": dashboard_owner_username(principal),
    }


@router.patch("/dashboards/metrics/{widget_key}")
def update_saved_dashboard_metric(
    widget_key: str,
    request: SavedMetricUpdateRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    metric = get_saved_metric_for_owner(session, widget_key, dashboard_owner_username(principal))
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved metric not found.",
        )

    label = request.label.strip()
    if not label:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Metric name is required.",
        )

    metric.label = label
    metric.updated_by = dashboard_owner_username(principal)
    session.commit()

    return {"item": serialize_saved_metric(session, metric, include_result=False)}


@router.delete("/dashboards/metrics/{widget_key}")
def delete_saved_dashboard_metric(
    widget_key: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    owner_user_id = dashboard_owner_user_id(principal)
    metric = get_saved_metric_for_owner(session, widget_key, dashboard_owner_username(principal))
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved metric not found.",
        )

    dashboard_widgets = list(
        session.scalars(
            select(DashboardWidget).where(
                DashboardWidget.metric_widget_key == widget_key,
                DashboardWidget.owner_user_id == owner_user_id,
            )
        )
    )
    for widget in dashboard_widgets:
        session.delete(widget)

    for metric_link in session.scalars(
        select(UserSavedMetric).where(UserSavedMetric.widget_key == widget_key)
    ):
        session.delete(metric_link)
    session.delete(metric)

    session.commit()

    return {
        "status": "deleted",
        "widget_key": widget_key,
        "removed_dashboard_widgets": len(dashboard_widgets),
    }


@router.post("/dashboards")
def create_dashboard(
    request: DashboardCreateRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dashboard name is required.",
        )

    dashboard = Dashboard(
        dashboard_id=uuid4().hex,
        owner_user_id=dashboard_owner_user_id(principal),
        owner_username=dashboard_owner_username(principal),
        name=name,
        description=request.description.strip(),
    )
    session.add(dashboard)
    state = get_or_create_dashboard_user_state(session, principal)
    state.active_dashboard_id = dashboard.dashboard_id
    session.commit()

    return {
        "item": serialize_dashboard(dashboard),
        "active_dashboard_id": dashboard.dashboard_id,
        "active_dashboard": serialize_dashboard(dashboard),
    }


@router.put("/dashboards/active")
def set_active_dashboard(
    request: ActiveDashboardRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    dashboard = get_dashboard_for_owner(
        session,
        request.dashboard_id,
        dashboard_owner_user_id(principal),
    )

    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    state = get_or_create_dashboard_user_state(session, principal)
    state.active_dashboard_id = dashboard.dashboard_id
    session.commit()

    return {
        "active_dashboard_id": dashboard.dashboard_id,
        "active_dashboard": serialize_dashboard(dashboard),
    }


@router.put("/dashboards/{dashboard_id}")
def update_dashboard(
    dashboard_id: str,
    request: DashboardUpdateRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    dashboard = get_dashboard_for_owner(
        session,
        dashboard_id,
        dashboard_owner_user_id(principal),
    )

    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    name = request.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dashboard name is required.",
        )

    dashboard.name = name
    dashboard.description = request.description.strip()
    dashboard.owner_username = dashboard_owner_username(principal)
    session.commit()

    return {"item": serialize_dashboard(dashboard)}


@router.get("/dashboards/{dashboard_id}/widgets")
def list_widgets_for_dashboard(
    dashboard_id: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    owner_user_id = dashboard_owner_user_id(principal)
    dashboard = get_dashboard_for_owner(session, dashboard_id, owner_user_id)
    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    widgets = list_dashboard_widgets(session, dashboard.dashboard_id, owner_user_id)
    return {
        "dashboard_id": dashboard.dashboard_id,
        "items": [serialize_dashboard_widget(session, widget) for widget in widgets],
    }


@router.post("/dashboards/{dashboard_id}/widgets")
def add_widget_to_dashboard(
    dashboard_id: str,
    request: DashboardWidgetCreateRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    owner_user_id = dashboard_owner_user_id(principal)
    dashboard = get_dashboard_for_owner(session, dashboard_id, owner_user_id)
    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    metric = get_saved_metric_for_owner(
        session,
        request.metric_widget_key,
        dashboard_owner_username(principal),
    )
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved metric not found.",
        )

    title = request.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Widget title is required.",
        )

    if request.visualization_type not in DASHBOARD_VISUALIZATION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported widget type.",
        )

    widgets = list_dashboard_widgets(session, dashboard.dashboard_id, owner_user_id)
    width, height = default_dashboard_widget_size(request.visualization_type)
    widget = DashboardWidget(
        widget_id=uuid4().hex,
        dashboard_id=dashboard.dashboard_id,
        owner_user_id=owner_user_id,
        owner_username=dashboard_owner_username(principal),
        metric_widget_key=metric.widget_key,
        title=title,
        visualization_type=request.visualization_type,
        x=0,
        y=next_dashboard_widget_position(widgets),
        w=width,
        h=height,
    )
    session.add(widget)
    session.commit()

    return {"item": serialize_dashboard_widget(session, widget)}


@router.patch("/dashboards/{dashboard_id}/widgets/layout")
def update_dashboard_widget_layout(
    dashboard_id: str,
    request: DashboardWidgetLayoutRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    owner_user_id = dashboard_owner_user_id(principal)
    dashboard = get_dashboard_for_owner(session, dashboard_id, owner_user_id)
    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    updated: list[DashboardWidget] = []
    for item in request.items:
        widget = get_dashboard_widget_for_owner(
            session,
            dashboard.dashboard_id,
            item.widget_id,
            owner_user_id,
        )
        if widget is None:
            continue
        widget.x = item.x
        widget.y = item.y
        widget.w = item.w
        widget.h = item.h
        updated.append(widget)

    session.commit()
    return {
        "dashboard_id": dashboard.dashboard_id,
        "items": [serialize_dashboard_widget(session, widget) for widget in updated],
    }


@router.delete("/dashboards/{dashboard_id}/widgets/{widget_id}")
def delete_dashboard_widget(
    dashboard_id: str,
    widget_id: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    owner_user_id = dashboard_owner_user_id(principal)
    dashboard = get_dashboard_for_owner(session, dashboard_id, owner_user_id)
    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    widget = get_dashboard_widget_for_owner(
        session,
        dashboard.dashboard_id,
        widget_id,
        owner_user_id,
    )
    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard widget not found.",
        )

    session.delete(widget)
    session.commit()
    return {"status": "deleted", "id": widget_id}


@router.delete("/dashboards/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    dashboard = get_dashboard_for_owner(
        session,
        dashboard_id,
        dashboard_owner_user_id(principal),
    )

    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )

    owner_user_id = dashboard_owner_user_id(principal)
    state = get_or_create_dashboard_user_state(session, principal)
    for widget in list_dashboard_widgets(session, dashboard.dashboard_id, owner_user_id):
        session.delete(widget)
    session.delete(dashboard)
    session.flush()

    if state.active_dashboard_id == dashboard_id:
        next_dashboard = session.scalar(
            select(Dashboard)
            .where(Dashboard.owner_user_id == owner_user_id)
            .order_by(Dashboard.updated_at.desc(), Dashboard.id.desc())
        )
        state.active_dashboard_id = next_dashboard.dashboard_id if next_dashboard else ""

    session.commit()

    return {
        "status": "deleted",
        "id": dashboard_id,
        "active_dashboard_id": state.active_dashboard_id,
    }
