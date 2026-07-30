from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from gaard_api.admin.database import get_session
from gaard_api.admin.models import AdminSession, AdminUser
from gaard_api.admin.security import hash_token
from gaard_api.license import license_service as license_service

SESSION_ACTIVITY_WRITE_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthenticatedSession:
    session: AdminSession
    user: AdminUser


def identity_id_for_principal(principal: object | None) -> str | None:
    if principal is None:
        return None

    principal_session = getattr(principal, "session", None)
    principal_user = getattr(principal, "user", None)
    auth_provider = str(
        getattr(principal_session, "auth_provider", "")
        or getattr(principal_user, "auth_provider", "")
        or ""
    )
    username = str(
        getattr(principal_session, "username", "")
        or getattr(principal_user, "username", "")
        or ""
    )

    if auth_provider == "local":
        user_id = getattr(principal_user, "id", None)
        if user_id is not None:
            return f"local:{user_id}"

    if auth_provider and username:
        return f"{auth_provider}:{username}"
    return None


def identity_ids_for_principal(principal: object | None) -> list[str]:
    primary = identity_id_for_principal(principal)
    ids = [primary] if primary is not None else []

    principal_session = getattr(principal, "session", None)
    principal_user = getattr(principal, "user", None)
    auth_provider = str(
        getattr(principal_session, "auth_provider", "")
        or getattr(principal_user, "auth_provider", "")
        or ""
    )
    username = str(
        getattr(principal_session, "username", "")
        or getattr(principal_user, "username", "")
        or ""
    )
    legacy_username_id = f"{auth_provider}:{username}" if auth_provider and username else ""
    if legacy_username_id and legacy_username_id not in ids:
        ids.append(legacy_username_id)

    return ids


def get_authorization_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header.",
        )

    return token

#w admin.py zdublowana ale w starej wersji 
def get_current_authenticated_session(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> AuthenticatedSession:
    token = get_authorization_token(authorization)
    token_hash = hash_token(token)

    admin_session = session.scalar(
        select(AdminSession).where(AdminSession.token_hash == token_hash)
    )

    if admin_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin session.",
        )

    user = session.get(AdminUser, admin_session.user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin session.",
        )
    if not user.is_system_admin and not license_service.identity_management_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive because the Enterprise license is not active.",
        )

    # Update activity in the authentication boundary so every authenticated flow is
    # covered, while the conditional update caps writes to one per session per 5 min.
    activity_cutoff = datetime.now(UTC) - SESSION_ACTIVITY_WRITE_INTERVAL
    result = session.execute(
        update(AdminSession)
        .where(AdminSession.id == admin_session.id, AdminSession.last_seen < activity_cutoff)
        .values(last_seen=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], result).rowcount:
        session.commit()
    else:
        # End the read transaction before the endpoint opens another metadata
        # session (some admin flows do this on SQLite).  This is a read-only
        # commit; the activity row itself remains untouched.
        session.commit()

    ensure_user_license_access(user)

    return AuthenticatedSession(session=admin_session, user=user)


#wersja z auth_dependencies.py
def get_current_api_user(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AuthenticatedSession:
    if principal.user.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user does not have API access.",
        )
    if principal.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change is required before using the application.",
        )
    return principal


def get_current_enterprise_api_user(
    principal: AuthenticatedSession = Depends(get_current_api_user),
) -> AuthenticatedSession:
    if principal.user.enterprise_access:
        return principal
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "This account has dashboard-only access because no Enterprise user "
            "license is assigned."
        ),
    )


def get_current_admin_allow_password_change(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AdminUser:
    if principal.user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role is required.",
        )
    return principal.user


def get_current_admin(
    user: AdminUser = Depends(get_current_admin_allow_password_change),
) -> AdminUser:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change is required before using the admin portal.",
        )

    return user

def get_current_enterprise_admin(
    user: AdminUser = Depends(get_current_admin),
) -> AdminUser:
    """Require an administrator with an assigned Enterprise user license."""
    if not user.enterprise_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An assigned Enterprise user license is required.",
        )
    return user
    
    
def has_enterprise_user_access(principal: AuthenticatedSession) -> bool:
    return bool(principal.user.enterprise_access)

def ensure_user_license_access(user: AdminUser) -> None:
    """Keep non-system accounts active only while global Enterprise is active."""
    if user.is_system_admin:
        return
    state = license_service.refresh_if_due()
    if state.features.get("identity_management"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This account does not have an active Enterprise user license.",
    )
