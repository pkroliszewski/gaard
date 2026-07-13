from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AuthenticatedIdentity:
    username: str
    role: str
    provider_id: str
    provider_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AuthenticationProvider(Protocol):
    provider_id: str
    provider_name: str

    def authenticate(
        self,
        session: Session,
        username: str,
        password: str,
    ) -> AuthenticatedIdentity | None:
        ...


class IdentityProvider(Protocol):
    provider_id: str
    provider_name: str

    def list_users(self, session: Session, refresh: bool = False) -> list[dict[str, Any]]:
        ...


class AuthProviderRegistry:
    """Ordered authentication provider chain supplied by extensions."""

    def __init__(self) -> None:
        self._providers: list[AuthenticationProvider] = []
        self._identity_providers: list[IdentityProvider] = []

    def register_identity_provider(self, provider: IdentityProvider) -> None:
        self._identity_providers.append(provider)

    def identity_providers(self) -> list[IdentityProvider]:
        return list(self._identity_providers)

    def register(self, provider: AuthenticationProvider) -> None:
        self._providers.append(provider)

    def authenticate(
        self,
        session: Session,
        username: str,
        password: str,
    ) -> AuthenticatedIdentity | None:
        for provider in self._providers:
            identity = provider.authenticate(session, username, password)
            if identity is not None:
                return identity
        return None

    def providers(self) -> list[AuthenticationProvider]:
        return list(self._providers)
