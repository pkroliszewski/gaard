from __future__ import annotations

from functools import lru_cache
import os
import ssl
from typing import Any
from urllib.parse import urlparse

import certifi
import httpx2 as httpx


def post(url: str, **kwargs: Any) -> httpx.Response:
    return request_with_certifi_fallback(httpx.post, url, **kwargs)


def get(url: str, **kwargs: Any) -> httpx.Response:
    return request_with_certifi_fallback(httpx.get, url, **kwargs)


def request_with_certifi_fallback(request: Any, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return request(url, **kwargs)
    except httpx.ConnectError as exc:
        if not should_retry_with_certifi(url, kwargs):
            raise
        try:
            return request(url, **{**kwargs, "verify": certifi_ssl_context()})
        except httpx.HTTPError as fallback_exc:
            raise fallback_exc from exc


def should_retry_with_certifi(url: str, kwargs: dict[str, Any]) -> bool:
    if kwargs.get("verify") is not None:
        return False
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return False
    return urlparse(url).scheme == "https"


@lru_cache(maxsize=1)
def certifi_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def http_error_summary(exc: httpx.HTTPError) -> str:
    detail = str(exc).strip()
    if detail:
        return f"{exc.__class__.__name__}: {detail}"
    return exc.__class__.__name__
