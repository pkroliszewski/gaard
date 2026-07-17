from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from gaard_api.admin.database import get_session
from gaard_api.admin.models import AdminSession, AdminUser
from gaard_api.admin.security import hash_token


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

    return AuthenticatedSession(session=admin_session, user=user)


def get_current_api_user(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AuthenticatedSession:
    if principal.user.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user does not have API access.",
        )
    return principal


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
