from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
import re
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4
import zipfile

import httpx2 as httpx
from gaard_core.errors import GaardError
from packaging.version import InvalidVersion, Version

from gaard_api.core.settings import settings
from gaard_api.license import LicensePlan, LicenseState, gaard_api_version
from gaard_api.tls_http import get as tls_get
from gaard_api.tls_http import http_error_summary, post as tls_post


logger = logging.getLogger(__name__)

PACKS_BY_LICENSE_PLAN: dict[LicensePlan, tuple[str, ...]] = {
    "community": (),
    "data_analyst": ("data-analyst",),
    "enterprise": ("data-analyst", "enterprise"),
}
PACKAGE_NAMES_BY_PACK: dict[str, tuple[str, ...]] = {
    "data-analyst": (
        "gaard-duckdb-excel-connector",
        "gaard-external-api",
        "gaard-multi-datasource-access",
    ),
    "enterprise": ("gaard-extract",),
}
PRIVATE_PACKAGE_NAMES = tuple(
    dict.fromkeys(
        package_name
        for package_names in PACKAGE_NAMES_BY_PACK.values()
        for package_name in package_names
    )
)
SAFE_FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.zip$")

HttpRequest = Callable[..., Any]
PackageVersionResolver = Callable[[str], str]
PipRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]
ProgressReporter = Callable[[str, int, str], None]


class PackageUpdateError(GaardError):
    code = "PACKAGE_UPDATE_FAILED"
    status_code = 502


class PackageUpdateInProgress(GaardError):
    code = "PACKAGE_UPDATE_IN_PROGRESS"
    status_code = 409


class PackageDownloadAccessError(GaardError):
    code = "PACKAGE_DOWNLOAD_NOT_ALLOWED"
    status_code = 403


@dataclass
class PackageUpdateJob:
    job_id: str
    status: str = "running"
    stage: str = "queued"
    percent: int = 0
    message: str = "Queued package update."
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def serialize(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PackageUpdateJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, PackageUpdateJob] = {}
        self._lock = threading.Lock()

    def create(self) -> PackageUpdateJob:
        job = PackageUpdateJob(job_id=str(uuid4()))
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> PackageUpdateJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, *, stage: str, percent: int, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            job.stage = stage
            job.percent = max(0, min(99, int(percent)))
            job.message = message
            job.updated_at = datetime.now(UTC)

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.stage = "complete"
            job.percent = 100
            job.message = str(result.get("message") or "Package update complete.")
            job.result = result
            job.updated_at = datetime.now(UTC)

    def fail(self, job_id: str, exc: Exception) -> None:
        code = getattr(exc, "code", exc.__class__.__name__)
        message = getattr(exc, "message", str(exc))
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.stage = "failed"
            job.percent = max(1, min(100, job.percent))
            job.message = str(message)
            job.error = {
                "code": str(code),
                "message": str(message),
            }
            job.updated_at = datetime.now(UTC)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._jobs.clear()


@dataclass(frozen=True)
class PackageArchive:
    pack: str
    file_name: str
    content: bytes
    sha256: str | None = None


@dataclass(frozen=True)
class ManifestPackage:
    name: str
    version: str
    path: str
    description: str


def default_pip_runner(
    args: list[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


class PackageUpdateService:
    def __init__(self) -> None:
        self._http_post: HttpRequest = tls_post
        self._http_get: HttpRequest = tls_get
        self._package_version: PackageVersionResolver = version
        self._pip_runner: PipRunner = default_pip_runner
        self._lock = threading.Lock()

    def reset_for_tests(self) -> None:
        self._http_post = tls_post
        self._http_get = tls_get
        self._package_version = version
        self._pip_runner = default_pip_runner

    def set_http_post_for_tests(self, http_post: HttpRequest) -> None:
        self._http_post = http_post

    def set_http_get_for_tests(self, http_get: HttpRequest) -> None:
        self._http_get = http_get

    def set_package_version_for_tests(
        self,
        package_version: PackageVersionResolver,
    ) -> None:
        self._package_version = package_version

    def set_pip_runner_for_tests(self, pip_runner: PipRunner) -> None:
        self._pip_runner = pip_runner

    def update_packages(
        self,
        *,
        license_state: LicenseState,
        license_key: str,
        instance_id: str,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise PackageUpdateInProgress("A package update is already running.")

        try:
            package_root = self._package_root()
            installed_versions = self._installed_versions(PRIVATE_PACKAGE_NAMES)
            self._report_progress(
                progress,
                "downloading",
                10,
                "Downloading package bundle from getgaard.com.",
            )
            archives = self._request_package_archives(
                license_key=license_key,
                instance_id=instance_id,
            )
            pack_results = self._process_archives(
                archives=archives,
                license_state=license_state,
                package_root=package_root,
                installed_versions=installed_versions,
                progress=progress,
            )
            installed_count = sum(
                1
                for pack_result in pack_results
                for package in pack_result.get("packages", [])
                if package.get("action") in {"installed", "upgraded"}
            )
            restart_required = installed_count > 0
            return {
                "status": "updated" if restart_required else "current",
                "plan": license_state.plan,
                "package_directory": str(package_root),
                "packs": pack_results,
                "installed_count": installed_count,
                "restart_required": restart_required,
                "message": self._result_message(installed_count, restart_required),
            }
        finally:
            self._lock.release()

    def _process_archives(
        self,
        *,
        archives: list[PackageArchive],
        license_state: LicenseState,
        package_root: Path,
        installed_versions: dict[str, str],
        progress: ProgressReporter | None = None,
    ) -> list[dict[str, Any]]:
        if not archives:
            self._report_progress(
                progress,
                "complete",
                100,
                "Packages are already up to date.",
            )
            return [
                {
                    "pack": pack,
                    "status": "current",
                    "packages": [
                        {
                            "name": package_name,
                            "installed_version": installed_versions.get(package_name),
                            "available_version": None,
                            "action": "current",
                        }
                        for package_name in PACKAGE_NAMES_BY_PACK.get(pack, ())
                    ],
                }
                for pack in PACKS_BY_LICENSE_PLAN[license_state.plan]
            ]

        self._report_progress(
            progress,
            "decompressing",
            35,
            "Decompressing downloaded package bundle.",
        )
        expanded_archives = [
            expanded_archive
            for archive in archives
            for expanded_archive in self._expand_archive(
                archive=archive,
                package_root=package_root,
            )
        ]
        self._report_progress(
            progress,
            "analyzing",
            55,
            "Analyzing package manifests and installed versions.",
        )
        processed_results = [
            self._process_archive(
                archive=archive,
                package_root=package_root,
                progress=progress,
            )
            for archive in expanded_archives
        ]
        packages = [
            package
            for processed_result in processed_results
            for package in processed_result["packages"]
        ]
        installed_count = sum(
            1
            for package in packages
            if package.get("action") in {"installed", "upgraded"}
        )
        status = "updated" if installed_count else "current"
        return [
            {
                "pack": self._pack_from_processed_result(processed_result),
                "status": processed_result["status"],
                "archives": [processed_result],
                "packages": processed_result["packages"],
            }
            for processed_result in processed_results
        ] or [
            {
                "pack": "download",
                "status": status,
                "archives": [],
                "packages": packages,
            }
        ]

    def _expand_archive(
        self,
        *,
        archive: PackageArchive,
        package_root: Path,
    ) -> list[PackageArchive]:
        manifest = self._read_manifest(archive.content)
        nested_archives = self._nested_package_archives(
            parent_archive=archive,
            manifest=manifest,
        )
        if not nested_archives:
            return [archive]

        self._save_archive(
            archive=archive,
            package_root=package_root,
            manifest=manifest,
        )
        return [
            expanded_archive
            for nested_archive in nested_archives
            for expanded_archive in self._expand_archive(
                archive=nested_archive,
                package_root=package_root,
            )
        ]

    def _nested_package_archives(
        self,
        *,
        parent_archive: PackageArchive,
        manifest: dict[str, Any],
    ) -> list[PackageArchive]:
        raw_packages = manifest.get("packages")
        if not isinstance(raw_packages, list):
            return []

        nested_archives: list[PackageArchive] = []
        for item in raw_packages:
            if not isinstance(item, dict):
                continue
            package_path = self._optional_text(item.get("path"))
            if not package_path or not package_path.endswith(".zip"):
                continue
            member_path = PurePosixPath(package_path)
            self._validate_package_member_path(member_path)
            content = self._zip_member_bytes(parent_archive.content, member_path)
            sha256 = self._optional_text(item.get("sha256"))
            self._validate_archive_sha256(content, sha256)
            nested_archives.append(
                PackageArchive(
                    pack=self._optional_text(item.get("plan")) or parent_archive.pack,
                    file_name=self._optional_text(item.get("file_name")) or member_path.name,
                    content=content,
                    sha256=sha256,
                )
            )

        return nested_archives

    def _request_package_archives(
        self,
        *,
        license_key: str,
        instance_id: str,
    ) -> list[PackageArchive]:
        payload = {
            "license_key": license_key,
            "product": "gaard",
            "gaard_version": gaard_api_version(),
            "instance_id": instance_id,
        }
        headers = {
            "Accept": "application/zip, application/json",
        }

        try:
            response = self._http_post(
                settings.gaard_package_download_url,
                json=payload,
                headers=headers,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise PackageUpdateError(
                "Package download failed before receiving a response: "
                f"{http_error_summary(exc)}."
            ) from exc

        status_code = int(response.status_code)
        if status_code in {204, 304}:
            return []
        if status_code in {401, 403}:
            raise PackageDownloadAccessError(
                "Package download was rejected by getgaard.com for the current license."
            )
        if status_code == 404:
            raise PackageUpdateError(
                "Package download endpoint was not found. Check GAARD_PACKAGE_DOWNLOAD_URL."
            )
        if status_code >= 400:
            detail = self._response_error_detail(response)
            suffix = f": {detail}" if detail else "."
            raise PackageUpdateError(
                f"Package download failed with HTTP {status_code}{suffix}"
            )

        content = bytes(response.content)
        content_type = str(response.headers.get("content-type", "")).lower()
        file_name = self._response_file_name(response) or "gaard-packages.zip"
        if self._is_zip_payload(content_type, content):
            return [PackageArchive(pack="download", file_name=file_name, content=content)]

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise PackageUpdateError(
                "Package download endpoint returned neither a ZIP nor valid JSON."
            ) from exc

        return self._archives_from_json_payload(
            pack="download",
            payload=payload,
            auth_headers=headers,
        )

    def _archives_from_json_payload(
        self,
        *,
        pack: str,
        payload: Any,
        auth_headers: dict[str, str],
    ) -> list[PackageArchive]:
        if not isinstance(payload, dict):
            raise PackageUpdateError("Package download response JSON must be an object.")

        status_value = str(payload.get("status") or "").lower()
        if status_value in {"current", "not_modified", "no_update", "no_updates"}:
            return []
        if payload.get("update_available") is False:
            return []

        items = self._download_items(payload)
        archives: list[PackageArchive] = []
        for item in items:
            archives.append(
                self._archive_from_download_item(
                    pack=pack,
                    item=item,
                    auth_headers=auth_headers,
                )
            )
        return archives

    def _download_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("packages", "downloads", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if any(
            payload.get(key)
            for key in (
                "download_url",
                "url",
                "content_base64",
                "package_base64",
                "zip_base64",
                "archive_base64",
            )
        ):
            return [payload]

        return []

    def _archive_from_download_item(
        self,
        *,
        pack: str,
        item: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> PackageArchive:
        sha256 = self._optional_text(item.get("sha256"))
        item_pack = self._optional_text(item.get("pack")) or self._optional_text(
            item.get("plan")
        ) or pack
        file_name = self._optional_text(item.get("file_name")) or self._optional_text(
            item.get("filename")
        )

        for key in ("content_base64", "package_base64", "zip_base64", "archive_base64"):
            content_base64 = self._optional_text(item.get(key))
            if content_base64:
                try:
                    content = base64.b64decode(content_base64, validate=True)
                except binascii.Error as exc:
                    raise PackageUpdateError("Package download response contains invalid base64.") from exc
                self._validate_archive_sha256(content, sha256)
                return PackageArchive(
                    pack=item_pack,
                    file_name=file_name or f"{pack}.zip",
                    content=content,
                    sha256=sha256,
                )

        download_url = self._optional_text(item.get("download_url")) or self._optional_text(
            item.get("url")
        )
        if not download_url:
            raise PackageUpdateError("Package download response is missing a download URL.")

        try:
            response = self._http_get(
                download_url,
                headers=auth_headers,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise PackageUpdateError(
                "Package file download failed before receiving a response: "
                f"{http_error_summary(exc)}."
            ) from exc

        status_code = int(response.status_code)
        if status_code >= 400:
            raise PackageUpdateError(f"Package file download failed with HTTP {status_code}.")

        content = bytes(response.content)
        self._validate_archive_sha256(content, sha256)
        return PackageArchive(
            pack=item_pack,
            file_name=file_name or self._file_name_from_url(download_url) or f"{pack}.zip",
            content=content,
            sha256=sha256,
        )

    def _process_archive(
        self,
        *,
        archive: PackageArchive,
        package_root: Path,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        self._validate_archive_sha256(archive.content, archive.sha256)
        manifest = self._read_manifest(archive.content)
        archive_path = self._save_archive(
            archive=archive,
            package_root=package_root,
            manifest=manifest,
        )

        manifest_packages = self._manifest_packages(manifest)
        package_actions = [
            self._package_action(manifest_package)
            for manifest_package in manifest_packages
        ]
        install_actions = [
            item
            for item in package_actions
            if item["action"] in {"install", "upgrade"}
        ]
        if not install_actions:
            return {
                "archive": str(archive_path),
                "status": "current",
                "manifest": self._public_manifest(manifest),
                "packages": [
                    {**item, "action": "current"}
                    for item in package_actions
                ],
            }

        self._report_progress(
            progress,
            "installing",
            75,
            f"Installing {len(install_actions)} package(s) with pip.",
        )
        self._extract_packages(
            archive_content=archive.content,
            manifest_packages=manifest_packages,
            package_root=package_root,
        )
        pip_result = self._install_manifest_packages(
            package_root=package_root,
            manifest_packages=[
                package
                for package in manifest_packages
                if package.name in {item["name"] for item in install_actions}
            ],
        )
        self._clear_extension_caches()

        installed_packages = []
        for item in package_actions:
            if item["action"] == "install":
                installed_packages.append({**item, "action": "installed"})
            elif item["action"] == "upgrade":
                installed_packages.append({**item, "action": "upgraded"})
            else:
                installed_packages.append({**item, "action": "current"})

        return {
            "archive": str(archive_path),
            "status": "updated",
            "manifest": self._public_manifest(manifest),
            "packages": installed_packages,
            "pip": pip_result,
        }

    def _save_archive(
        self,
        *,
        archive: PackageArchive,
        package_root: Path,
        manifest: dict[str, Any],
    ) -> Path:
        downloads_dir = package_root / ".downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        manifest_file_name = self._optional_text(manifest.get("file_name"))
        archive_path = downloads_dir / self._safe_file_name(
            manifest_file_name or archive.file_name
        )
        archive_path.write_bytes(archive.content)
        return archive_path

    def _pack_from_processed_result(self, processed_result: dict[str, Any]) -> str:
        manifest = processed_result.get("manifest")
        if isinstance(manifest, dict):
            plan = self._optional_text(manifest.get("plan"))
            if plan:
                return plan
            name = self._optional_text(manifest.get("name"))
            if name:
                return name
        return "download"

    def _install_manifest_packages(
        self,
        *,
        package_root: Path,
        manifest_packages: Iterable[ManifestPackage],
    ) -> dict[str, Any]:
        package_paths = [
            str(self._package_source_path(package_root, manifest_package.path))
            for manifest_package in manifest_packages
        ]
        if not package_paths:
            return {
                "returncode": 0,
                "output": "",
            }

        args = [sys.executable, "-m", "pip", "install", "-U", *package_paths]
        timeout = max(1, int(settings.gaard_package_install_timeout_seconds or 600))
        try:
            completed = self._pip_runner(args, timeout)
        except subprocess.TimeoutExpired as exc:
            raise PackageUpdateError("Package installation timed out.") from exc

        output = self._tail("\n".join([completed.stdout or "", completed.stderr or ""]))
        if completed.returncode != 0:
            raise PackageUpdateError(
                "Package installation failed. "
                f"pip exited with {completed.returncode}: {output}"
            )

        logger.info("Installed GAARD packages with %s", " ".join(args))
        return {
            "returncode": completed.returncode,
            "output": output,
        }

    def _package_action(self, manifest_package: ManifestPackage) -> dict[str, Any]:
        installed_version = self._installed_version(manifest_package.name)
        action = "install"
        if installed_version is not None:
            action = (
                "upgrade"
                if self._version_less_than(installed_version, manifest_package.version)
                else "current"
            )

        return {
            "name": manifest_package.name,
            "installed_version": installed_version,
            "available_version": manifest_package.version,
            "action": action,
        }

    def _installed_versions(self, package_names: Iterable[str]) -> dict[str, str]:
        return {
            package_name: installed_version
            for package_name in package_names
            if (installed_version := self._installed_version(package_name)) is not None
        }

    def _installed_version(self, package_name: str) -> str | None:
        try:
            return self._package_version(package_name)
        except PackageNotFoundError:
            return None

    def _version_less_than(self, installed: str, available: str) -> bool:
        try:
            return Version(installed) < Version(available)
        except InvalidVersion as exc:
            raise PackageUpdateError(
                f"Cannot compare package versions {installed!r} and {available!r}."
            ) from exc

    def _extract_packages(
        self,
        *,
        archive_content: bytes,
        manifest_packages: list[ManifestPackage],
        package_root: Path,
    ) -> None:
        package_root_resolved = package_root.resolve()
        package_paths = {
            PurePosixPath(manifest_package.path)
            for manifest_package in manifest_packages
        }

        for manifest_package in manifest_packages:
            package_path = self._package_source_path(package_root, manifest_package.path)
            if package_path.exists():
                shutil.rmtree(package_path)

        with zipfile.ZipFile(io.BytesIO(archive_content)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_path = PurePosixPath(info.filename)
                if not self._is_manifest_package_member(member_path, package_paths):
                    continue
                if self._is_zip_symlink(info):
                    raise PackageUpdateError("Package archives must not contain symlinks.")
                target = self._zip_member_target(package_root, member_path)
                target.resolve().relative_to(package_root_resolved)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

    def _is_manifest_package_member(
        self,
        member_path: PurePosixPath,
        package_paths: set[PurePosixPath],
    ) -> bool:
        for package_path in package_paths:
            try:
                member_path.relative_to(package_path)
                return True
            except ValueError:
                continue
        return False

    def _zip_member_target(self, package_root: Path, member_path: PurePosixPath) -> Path:
        self._validate_package_member_path(member_path)
        return package_root.joinpath(*member_path.parts[1:])

    def _validate_package_member_path(self, member_path: PurePosixPath) -> None:
        parts = member_path.parts
        if len(parts) < 2 or parts[0] != "packages":
            raise PackageUpdateError(f"Invalid package archive path: {member_path}")
        if any(part in {"", ".", ".."} for part in parts):
            raise PackageUpdateError(f"Unsafe package archive path: {member_path}")

    def _package_source_path(self, package_root: Path, manifest_path: str) -> Path:
        member_path = PurePosixPath(manifest_path)
        return self._zip_member_target(package_root, member_path)

    def _zip_member_bytes(self, archive_content: bytes, member_path: PurePosixPath) -> bytes:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_content)) as archive:
                info = archive.getinfo(str(member_path))
                if info.is_dir() or self._is_zip_symlink(info):
                    raise PackageUpdateError(f"Invalid nested package archive: {member_path}")
                with archive.open(info) as handle:
                    return handle.read()
        except KeyError as exc:
            raise PackageUpdateError(
                f"Nested package archive not found: {member_path}"
            ) from exc
        except zipfile.BadZipFile as exc:
            raise PackageUpdateError("Package archive is not a valid ZIP file.") from exc

    def _read_manifest(self, archive_content: bytes) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_content)) as archive:
                with archive.open("manifest.json") as handle:
                    manifest = json.loads(handle.read().decode("utf-8"))
        except (KeyError, json.JSONDecodeError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise PackageUpdateError("Package archive is missing a valid manifest.json.") from exc

        if not isinstance(manifest, dict):
            raise PackageUpdateError("Package archive manifest must be a JSON object.")
        return manifest

    def _manifest_packages(self, manifest: dict[str, Any]) -> list[ManifestPackage]:
        raw_packages = manifest.get("packages")
        if not isinstance(raw_packages, list) or not raw_packages:
            raise PackageUpdateError("Package archive manifest must contain packages.")

        manifest_packages: list[ManifestPackage] = []
        for item in raw_packages:
            if not isinstance(item, dict):
                raise PackageUpdateError("Package manifest entries must be objects.")
            name = self._required_text(item.get("name"), "package name")
            package_version = self._required_text(item.get("version"), f"{name} version")
            package_path = self._required_text(item.get("path"), f"{name} path")
            if not package_path.startswith("packages/"):
                raise PackageUpdateError(f"Package {name} has an invalid source path.")
            manifest_packages.append(
                ManifestPackage(
                    name=name,
                    version=package_version,
                    path=package_path,
                    description=self._optional_text(item.get("description")) or "",
                )
            )
        return manifest_packages

    def _public_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            key: manifest[key]
            for key in (
                "name",
                "version",
                "plan",
                "gaard_version",
                "description",
                "created_at",
                "commit",
            )
            if key in manifest
        }

    def _package_root(self) -> Path:
        configured = str(settings.gaard_package_directory or "extensions").strip() or "extensions"
        path = Path(configured)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _response_file_name(self, response: Any) -> str | None:
        content_disposition = str(response.headers.get("content-disposition", ""))
        match = re.search(r'filename="?([^";]+)"?', content_disposition)
        return match.group(1) if match else None

    def _response_error_detail(self, response: Any) -> str:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None

        if isinstance(payload, dict):
            candidates = [
                payload.get("detail"),
                payload.get("message"),
                payload.get("error"),
            ]
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    return self._tail(candidate, limit=1000)
                if isinstance(candidate, dict):
                    for key in ("message", "detail", "code"):
                        value = candidate.get(key)
                        if isinstance(value, str) and value.strip():
                            return self._tail(value, limit=1000)
            return self._tail(json.dumps(payload, ensure_ascii=False), limit=1000)

        text = str(getattr(response, "text", "") or "").strip()
        return self._tail(text, limit=1000)

    def _file_name_from_url(self, url: str) -> str | None:
        file_name = Path(unquote(urlparse(url).path)).name
        return file_name or None

    def _safe_file_name(self, value: str) -> str:
        file_name = Path(value).name
        if not SAFE_FILE_NAME_PATTERN.fullmatch(file_name):
            raise PackageUpdateError(f"Unsafe package archive file name: {value!r}.")
        return file_name

    def _is_zip_payload(self, content_type: str, content: bytes) -> bool:
        return "zip" in content_type or content.startswith(b"PK\x03\x04")

    def _is_zip_symlink(self, info: zipfile.ZipInfo) -> bool:
        file_type = (info.external_attr >> 16) & 0o170000
        return file_type == stat.S_IFLNK

    def _validate_archive_sha256(self, content: bytes, expected_sha256: str | None) -> None:
        if not expected_sha256:
            return
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            raise PackageUpdateError("Package archive checksum verification failed.")

    def _required_text(self, value: Any, label: str) -> str:
        text = self._optional_text(value)
        if not text:
            raise PackageUpdateError(f"Package archive manifest is missing {label}.")
        return text

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _tail(self, value: str, limit: int = 4000) -> str:
        text = value.strip()
        return text[-limit:] if len(text) > limit else text

    def _clear_extension_caches(self) -> None:
        from gaard_api.extensions import (
            get_connector_registry,
            get_extension_manager,
            get_query_hook_registry,
        )

        get_extension_manager.cache_clear()
        get_connector_registry.cache_clear()
        get_query_hook_registry.cache_clear()

    def _result_message(self, installed_count: int, restart_required: bool) -> str:
        if installed_count == 0:
            return "Packages are already up to date."
        suffix = (
            " Restart GAARD to activate any newly installed extension routes."
            if restart_required
            else ""
        )
        return f"Installed or updated {installed_count} package(s).{suffix}"

    def _report_progress(
        self,
        progress: ProgressReporter | None,
        stage: str,
        percent: int,
        message: str,
    ) -> None:
        if progress is not None:
            progress(stage, percent, message)


package_update_service = PackageUpdateService()
package_update_jobs = PackageUpdateJobStore()
