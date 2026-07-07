from __future__ import annotations

import ssl

import httpx2 as httpx
import pytest

from gaard_api.tls_http import request_with_certifi_fallback


def test_https_connect_error_retries_with_certifi_context() -> None:
    calls: list[dict] = []

    def fake_request(url: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ConnectError(("certificate is not trusted",))
        return httpx.Response(200, json={"ok": True})

    response = request_with_certifi_fallback(
        fake_request,
        "https://getgaard.com/api/license/validate",
        json={"product": "gaard"},
        timeout=10.0,
    )

    assert response.status_code == 200
    assert "verify" not in calls[0]
    assert isinstance(calls[1]["verify"], ssl.SSLContext)


def test_connect_error_does_not_retry_when_verify_is_explicit() -> None:
    calls = 0

    def fake_request(url: str, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(("certificate is not trusted",))

    context = ssl.create_default_context()
    with pytest.raises(httpx.ConnectError):
        request_with_certifi_fallback(
            fake_request,
            "https://getgaard.com/api/license/validate",
            verify=context,
            timeout=10.0,
        )

    assert calls == 1


def test_connect_error_does_not_retry_when_ssl_cert_file_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_request(url: str, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(("certificate is not trusted",))

    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    with pytest.raises(httpx.ConnectError):
        request_with_certifi_fallback(
            fake_request,
            "https://getgaard.com/api/license/validate",
            timeout=10.0,
        )

    assert calls == 1
