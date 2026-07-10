from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gaard_api.admin.database import get_session
from gaard_api.admin.models import Dashboard, DashboardUserState
from gaard_api.auth_dependencies import AuthenticatedSession, get_current_api_user

router = APIRouter()


class DashboardCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)


class ActiveDashboardRequest(BaseModel):
    dashboard_id: str = Field(min_length=1, max_length=64)


def serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def dashboard_owner_user_id(principal: AuthenticatedSession) -> str:
    return str(principal.user.id)


def dashboard_owner_username(principal: AuthenticatedSession) -> str:
    return principal.session.username or principal.user.username


def serialize_dashboard(dashboard: Dashboard) -> dict[str, Any]:
    return {
        "id": dashboard.dashboard_id,
        "name": dashboard.name,
        "description": dashboard.description,
        "owner_user_id": dashboard.owner_user_id,
        "owner_username": dashboard.owner_username,
        "created_at": serialize_datetime(dashboard.created_at),
        "updated_at": serialize_datetime(dashboard.updated_at),
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
