from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from gaard_api.admin.database import get_session
from gaard_api.admin.models import (
    AdminUser,
    Dashboard,
    DashboardShare,
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

DASHBOARD_SHARE_ACCESS_LEVELS = {"view", "edit"}


class DashboardWriteRequest(BaseModel):
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


class DashboardShareUpdateItem(BaseModel):
    user_id: str = Field(min_length=1, max_length=255)
    access_level: str = Field(pattern=r"^(view|edit)$")


class DashboardShareUpdateRequest(BaseModel):
    items: list[DashboardShareUpdateItem] = Field(default_factory=list)


def dashboard_owner_user_id(principal: AuthenticatedSession) -> str:
    return str(principal.user.id)


def dashboard_owner_username(principal: AuthenticatedSession) -> str:
    return principal.session.username or principal.user.username


def saved_metric_tag_names(owner_username: str) -> tuple[str, str]:
    return ("public", owner_username)


def serialize_dashboard(
    dashboard: Dashboard,
    *,
    access_level: str = "owner",
    is_owner: bool = True,
) -> dict[str, Any]:
    can_edit = is_owner or access_level == "edit"
    return {
        "id": dashboard.dashboard_id,
        "name": dashboard.name,
        "description": dashboard.description,
        "owner_user_id": dashboard.owner_user_id,
        "owner_username": dashboard.owner_username,
        "access_level": "owner" if is_owner else access_level,
        "is_owner": is_owner,
        "can_edit": can_edit,
        "can_share": can_edit,
        "can_delete": is_owner,
        "shared": not is_owner,
        "created_at": serialize_datetime(dashboard.created_at),
        "updated_at": serialize_datetime(dashboard.updated_at),
    }


def serialize_dashboard_share(share: DashboardShare) -> dict[str, Any]:
    return {
        "user_id": share.target_user_id,
        "username": share.target_username,
        "access_level": share.access_level,
        "created_by_user_id": share.created_by_user_id,
        "created_by_username": share.created_by_username,
        "created_at": serialize_datetime(share.created_at),
        "updated_at": serialize_datetime(share.updated_at),
    }


def serialize_share_user(user: AdminUser) -> dict[str, Any]:
    return {
        "user_id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "name": user.display_name or user.username,
        "role": user.role,
    }


def dashboard_share_for_principal(
    session: Session,
    dashboard_id: str,
    principal: AuthenticatedSession,
) -> DashboardShare | None:
    return session.scalar(
        select(DashboardShare).where(
            DashboardShare.dashboard_id == dashboard_id,
            DashboardShare.target_user_id == dashboard_owner_user_id(principal),
        )
    )


def dashboard_access_level(
    session: Session,
    dashboard: Dashboard,
    principal: AuthenticatedSession,
) -> tuple[str, bool] | None:
    owner_user_id = dashboard_owner_user_id(principal)
    if dashboard.owner_user_id == owner_user_id:
        return "owner", True

    share = dashboard_share_for_principal(session, dashboard.dashboard_id, principal)
    if share is None or share.access_level not in DASHBOARD_SHARE_ACCESS_LEVELS:
        return None
    return share.access_level, False


def serialize_dashboard_for_principal(
    session: Session,
    dashboard: Dashboard,
    principal: AuthenticatedSession,
) -> dict[str, Any]:
    access = dashboard_access_level(session, dashboard, principal)
    if access is None:
        return serialize_dashboard(dashboard, access_level="view", is_owner=False)
    access_level, is_owner = access
    return serialize_dashboard(dashboard, access_level=access_level, is_owner=is_owner)


def get_dashboard_with_access(
    session: Session,
    dashboard_id: str,
    principal: AuthenticatedSession,
) -> tuple[Dashboard, str, bool] | None:
    dashboard = session.scalar(select(Dashboard).where(Dashboard.dashboard_id == dashboard_id))
    if dashboard is None:
        return None

    access = dashboard_access_level(session, dashboard, principal)
    if access is None:
        return None
    access_level, is_owner = access
    return dashboard, access_level, is_owner


def can_edit_dashboard(access_level: str, is_owner: bool) -> bool:
    return is_owner or access_level == "edit"


def can_delete_dashboard(is_owner: bool) -> bool:
    return is_owner


def ensure_dashboard_edit_access(access_level: str, is_owner: bool) -> None:
    if not can_edit_dashboard(access_level, is_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard edit access is required.",
        )


def list_dashboard_share_users(session: Session) -> list[AdminUser]:
    return list(session.scalars(select(AdminUser).order_by(AdminUser.username.asc(), AdminUser.id.asc())))


def list_dashboard_shares(session: Session, dashboard_id: str) -> list[DashboardShare]:
    return list(
        session.scalars(
            select(DashboardShare)
            .where(DashboardShare.dashboard_id == dashboard_id)
            .order_by(DashboardShare.target_username.asc(), DashboardShare.id.asc())
        )
    )


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


def list_accessible_dashboards(
    session: Session,
    principal: AuthenticatedSession,
) -> list[Dashboard]:
    owner_user_id = dashboard_owner_user_id(principal)
    dashboards_by_id: dict[str, Dashboard] = {
        dashboard.dashboard_id: dashboard
        for dashboard in list_owner_dashboards(session, owner_user_id)
    }
    shared_dashboard_ids = list(
        session.scalars(
            select(DashboardShare.dashboard_id).where(
                DashboardShare.target_user_id == owner_user_id
            )
        )
    )
    if shared_dashboard_ids:
        for dashboard in session.scalars(
            select(Dashboard).where(Dashboard.dashboard_id.in_(shared_dashboard_ids))
        ):
            dashboards_by_id.setdefault(dashboard.dashboard_id, dashboard)

    return sorted(
        dashboards_by_id.values(),
        key=lambda dashboard: (dashboard.updated_at, dashboard.id),
        reverse=True,
    )


def list_dashboard_widgets(
    session: Session,
    dashboard_id: str,
) -> list[DashboardWidget]:
    return list(
        session.scalars(
            select(DashboardWidget)
            .where(DashboardWidget.dashboard_id == dashboard_id)
            .order_by(DashboardWidget.y.asc(), DashboardWidget.x.asc(), DashboardWidget.id.asc())
        )
    )


def get_dashboard_widget(
    session: Session,
    dashboard_id: str,
    widget_id: str,
) -> DashboardWidget | None:
    return session.scalar(
        select(DashboardWidget).where(
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.widget_id == widget_id,
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
    dashboards = list_accessible_dashboards(session, principal)
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
        "items": [
            serialize_dashboard_for_principal(session, dashboard, principal)
            for dashboard in dashboards
        ],
        "active_dashboard_id": active_dashboard_id,
        "active_dashboard": serialize_dashboard_for_principal(session, active_dashboard, principal)
        if active_dashboard is not None
        else None,
        "viewer": dashboard_owner_username(principal),
        "viewer_user_id": dashboard_owner_user_id(principal),
        "share_user_count": len(list_dashboard_share_users(session)),
    }


@router.get("/dashboards")
def list_dashboards(
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload = serialize_dashboard_collection(session, principal)
    session.commit()

    return payload


@router.get("/dashboards/share-users")
def list_dashboard_users_for_sharing(
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    users = list_dashboard_share_users(session)
    return {
        "items": [serialize_share_user(user) for user in users],
        "total_users": len(users),
        "viewer_user_id": dashboard_owner_user_id(principal),
        "viewer": dashboard_owner_username(principal),
    }


@router.get("/dashboards/{dashboard_id}/shares")
def get_dashboard_shares(
    dashboard_id: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)
    shares = list_dashboard_shares(session, dashboard.dashboard_id)
    users = list_dashboard_share_users(session)
    return {
        "dashboard_id": dashboard.dashboard_id,
        "items": [serialize_dashboard_share(share) for share in shares],
        "users": [serialize_share_user(user) for user in users],
        "total_users": len(users),
    }


@router.put("/dashboards/{dashboard_id}/shares")
def update_dashboard_shares(
    dashboard_id: str,
    request: DashboardShareUpdateRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)

    requested_by_user_id = dashboard_owner_user_id(principal)
    requested_by_username = dashboard_owner_username(principal)
    users_by_id = {
        str(user.id): user
        for user in session.scalars(select(AdminUser))
    }
    existing_shares = {
        share.target_user_id: share
        for share in list_dashboard_shares(session, dashboard.dashboard_id)
    }
    next_target_ids: set[str] = set()

    for item in request.items:
        target_user_id = item.user_id.strip()
        if not target_user_id or target_user_id == dashboard.owner_user_id:
            continue
        target_user = users_by_id.get(target_user_id)
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Share target user {target_user_id} does not exist.",
            )
        if item.access_level not in DASHBOARD_SHARE_ACCESS_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dashboard share access level must be view or edit.",
            )

        next_target_ids.add(target_user_id)
        share = existing_shares.get(target_user_id)
        if share is None:
            share = DashboardShare(
                dashboard_id=dashboard.dashboard_id,
                target_user_id=target_user_id,
                target_username=target_user.username,
                access_level=item.access_level,
                created_by_user_id=requested_by_user_id,
                created_by_username=requested_by_username,
            )
            session.add(share)
            existing_shares[target_user_id] = share
        else:
            share.target_username = target_user.username
            share.access_level = item.access_level
            share.created_by_user_id = share.created_by_user_id or requested_by_user_id
            share.created_by_username = share.created_by_username or requested_by_username

    for target_user_id, share in existing_shares.items():
        if target_user_id not in next_target_ids:
            session.delete(share)

    session.commit()
    shares = list_dashboard_shares(session, dashboard.dashboard_id)
    return {
        "dashboard_id": dashboard.dashboard_id,
        "items": [serialize_dashboard_share(share) for share in shares],
    }


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
    request: DashboardWriteRequest,
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
    access = get_dashboard_with_access(session, request.dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access

    state = get_or_create_dashboard_user_state(session, principal)
    state.active_dashboard_id = dashboard.dashboard_id
    session.commit()

    return {
        "active_dashboard_id": dashboard.dashboard_id,
        "active_dashboard": serialize_dashboard(
            dashboard,
            access_level=access_level,
            is_owner=is_owner,
        ),
    }


@router.put("/dashboards/{dashboard_id}")
def update_dashboard(
    dashboard_id: str,
    request: DashboardWriteRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)

    name = request.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dashboard name is required.",
        )

    dashboard.name = name
    dashboard.description = request.description.strip()
    session.commit()

    return {
        "item": serialize_dashboard(
            dashboard,
            access_level=access_level,
            is_owner=is_owner,
        )
    }


@router.get("/dashboards/{dashboard_id}/widgets")
def list_widgets_for_dashboard(
    dashboard_id: str,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, _access_level, _is_owner = access

    widgets = list_dashboard_widgets(session, dashboard.dashboard_id)
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
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)

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

    widgets = list_dashboard_widgets(session, dashboard.dashboard_id)
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
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)

    updated: list[DashboardWidget] = []
    for item in request.items:
        widget = get_dashboard_widget(
            session,
            dashboard.dashboard_id,
            item.widget_id,
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
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, access_level, is_owner = access
    ensure_dashboard_edit_access(access_level, is_owner)

    widget = get_dashboard_widget(
        session,
        dashboard.dashboard_id,
        widget_id,
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
    access = get_dashboard_with_access(session, dashboard_id, principal)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found.",
        )
    dashboard, _access_level, is_owner = access
    if not can_delete_dashboard(is_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the dashboard owner can delete it.",
        )

    state = get_or_create_dashboard_user_state(session, principal)
    state_was_active = state.active_dashboard_id == dashboard_id
    for widget in list_dashboard_widgets(session, dashboard.dashboard_id):
        session.delete(widget)
    for share in list_dashboard_shares(session, dashboard.dashboard_id):
        session.delete(share)
    session.delete(dashboard)
    session.flush()

    for user_state in session.scalars(
        select(DashboardUserState).where(DashboardUserState.active_dashboard_id == dashboard_id)
    ):
        user_state.active_dashboard_id = ""

    if state_was_active:
        next_dashboard = next(iter(list_accessible_dashboards(session, principal)), None)
        state.active_dashboard_id = next_dashboard.dashboard_id if next_dashboard else ""

    session.commit()

    return {
        "status": "deleted",
        "id": dashboard_id,
        "active_dashboard_id": state.active_dashboard_id,
    }
