from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


_EXTENSION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SECTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


@dataclass(frozen=True, slots=True)
class AdminExtensionSection:
    extension_id: str
    section_key: str
    label: str
    path: str
    order: int = 1000
    description: str = ""

    @property
    def section_id(self) -> str:
        return f"extension:{self.extension_id}:{self.section_key}"

    def serialize(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "extension_id": self.extension_id,
            "section_key": self.section_key,
            "label": self.label,
            "description": self.description,
            "path": self.path,
            "order": self.order,
        }


@dataclass(frozen=True, slots=True)
class _RouterRegistration:
    extension_id: str
    router: APIRouter
    prefix: str
    tags: tuple[str, ...]
    dependencies: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _StaticAssetsRegistration:
    extension_id: str
    directory: Path
    path: str
    name: str


@dataclass(frozen=True, slots=True)
class _AdminPageRegistration:
    extension_id: str
    section_key: str
    html_path: Path
    path: str


class ApiRegistry:
    """Registry for trusted API/Admin contributions supplied by extensions."""

    def __init__(self, dependencies: Sequence[Any] | None = None) -> None:
        self._routers: list[_RouterRegistration] = []
        self._admin_sections: dict[str, AdminExtensionSection] = {}
        self._static_assets: list[_StaticAssetsRegistration] = []
        self._admin_pages: list[_AdminPageRegistration] = []
        self._initializers: list[Callable[[], None]] = []
        self._dependencies = tuple(dependencies or ())
        self._applied = False

    def register_router(
        self,
        *,
        extension_id: str,
        router: APIRouter,
        prefix: str = "",
        tags: Sequence[str] | None = None,
        dependencies: Sequence[Any] | None = None,
        require_admin: bool = True,
    ) -> None:
        extension_id = self._validate_extension_id(extension_id)
        normalized_prefix = self._normalize_relative_prefix(prefix)
        inherited_dependencies = self._dependencies if require_admin else ()
        self._routers.append(
            _RouterRegistration(
                extension_id=extension_id,
                router=router,
                prefix=f"/api/v1/extensions/{extension_id}{normalized_prefix}",
                tags=tuple(tags or (f"extension:{extension_id}",)),
                dependencies=(*inherited_dependencies, *(dependencies or ())),
            )
        )

    def register_admin_section(
        self,
        *,
        extension_id: str,
        section_key: str,
        label: str,
        path: str | None = None,
        order: int = 1000,
        description: str = "",
    ) -> None:
        extension_id = self._validate_extension_id(extension_id)
        section_key = self._validate_section_key(section_key)
        if not label.strip():
            raise ValueError("Admin extension section label must not be empty.")

        section = AdminExtensionSection(
            extension_id=extension_id,
            section_key=section_key,
            label=label,
            path=path or f"/admin/extensions/{extension_id}/{section_key}",
            order=order,
            description=description,
        )
        self._validate_admin_path(section.path, extension_id)
        self._admin_sections[section.section_id] = section

    def register_admin_page(
        self,
        *,
        extension_id: str,
        section_key: str,
        html_path: str | Path,
        label: str,
        order: int = 1000,
        description: str = "",
    ) -> None:
        extension_id = self._validate_extension_id(extension_id)
        section_key = self._validate_section_key(section_key)
        html_file = Path(html_path)
        if html_file.name == "":
            raise ValueError("Admin extension page must point to an HTML file.")

        path = f"/admin/extensions/{extension_id}/{section_key}"
        self._admin_pages.append(
            _AdminPageRegistration(
                extension_id=extension_id,
                section_key=section_key,
                html_path=html_file,
                path=path,
            )
        )
        self.register_admin_section(
            extension_id=extension_id,
            section_key=section_key,
            label=label,
            path=path,
            order=order,
            description=description,
        )

    def register_static_assets(
        self,
        *,
        extension_id: str,
        directory: str | Path,
        path: str | None = None,
        name: str | None = None,
    ) -> None:
        extension_id = self._validate_extension_id(extension_id)
        mount_path = path or f"/admin/extensions/{extension_id}/assets"
        self._validate_admin_path(mount_path, extension_id)
        self._static_assets.append(
            _StaticAssetsRegistration(
                extension_id=extension_id,
                directory=Path(directory),
                path=mount_path.rstrip("/"),
                name=name or f"extension-{extension_id}-assets",
            )
        )

    def register_initializer(self, initializer: Callable[[], None]) -> None:
        self._initializers.append(initializer)

    def list_admin_sections(self) -> list[AdminExtensionSection]:
        return sorted(
            self._admin_sections.values(),
            key=lambda section: (section.order, section.label.casefold(), section.section_id),
        )

    def apply_to(self, app: FastAPI) -> None:
        if self._applied:
            return

        for initializer in self._initializers:
            initializer()

        for static_assets in self._static_assets:
            if static_assets.directory.exists():
                app.mount(
                    static_assets.path,
                    StaticFiles(directory=static_assets.directory),
                    name=static_assets.name,
                )

        for admin_page in self._admin_pages:
            app.add_api_route(
                admin_page.path,
                _file_response_factory(admin_page.html_path),
                include_in_schema=False,
            )

        for registration in self._routers:
            app.include_router(
                registration.router,
                prefix=registration.prefix,
                tags=list(registration.tags),
                dependencies=list(registration.dependencies),
            )

        self._applied = True

    @staticmethod
    def _validate_extension_id(extension_id: str) -> str:
        if not _EXTENSION_ID_PATTERN.fullmatch(extension_id):
            raise ValueError(
                "Extension id must use lowercase letters, digits, and hyphens, "
                "and must start with a letter."
            )
        return extension_id

    @staticmethod
    def _validate_section_key(section_key: str) -> str:
        if not _SECTION_KEY_PATTERN.fullmatch(section_key):
            raise ValueError(
                "Admin extension section key must use lowercase letters, digits, and hyphens, "
                "and must start with a letter."
            )
        return section_key

    @staticmethod
    def _normalize_relative_prefix(prefix: str) -> str:
        if not prefix:
            return ""
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        if prefix.endswith("/"):
            prefix = prefix.rstrip("/")
        return prefix

    @staticmethod
    def _validate_admin_path(path: str, extension_id: str) -> None:
        expected_prefix = f"/admin/extensions/{extension_id}"
        if path != expected_prefix and not path.startswith(f"{expected_prefix}/"):
            raise ValueError(
                "Admin extension paths must stay below "
                f"{expected_prefix!r}."
            )


def _file_response_factory(path: Path) -> Callable[[], FileResponse]:
    def serve_file() -> FileResponse:
        return FileResponse(path)

    return serve_file
