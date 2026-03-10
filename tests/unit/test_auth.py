"""Unit tests for client_reporting_api.auth (GoogleOAuthMiddleware)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from client_reporting_api.auth import GoogleOAuthMiddleware


def _make_app(
    client_id: str = "test-client", allowed_domains: list[str] | None = None
) -> Starlette:
    async def homepage(request: Request) -> Response:
        return Response("OK", status_code=200)

    app = Starlette(routes=[Route("/", homepage), Route("/health", homepage)])
    app.add_middleware(GoogleOAuthMiddleware, client_id=client_id, allowed_domains=allowed_domains)
    return app


def test_missing_bearer_token_returns_401() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/", headers={})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Bearer token"


def test_valid_token_passes_through() -> None:
    fake_claims = {"sub": "1234", "email": "user@example.com", "hd": "example.com"}
    with patch(
        "client_reporting_api._google_auth_sync.verify_oauth2_token_sync",
        return_value=fake_claims,
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer fake-token"})
    assert response.status_code == 200


def test_wrong_domain_returns_403() -> None:
    fake_claims = {"sub": "1234", "email": "user@other.com", "hd": "other.com"}
    with patch(
        "client_reporting_api._google_auth_sync.verify_oauth2_token_sync",
        return_value=fake_claims,
    ):
        client = TestClient(
            _make_app(allowed_domains=["allowed.com"]), raise_server_exceptions=False
        )
        response = client.get("/", headers={"Authorization": "Bearer fake-token"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Domain not allowed"


def test_health_path_skips_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 200


def test_invalid_token_returns_401() -> None:
    from google.auth.exceptions import GoogleAuthError

    with patch(
        "client_reporting_api._google_auth_sync.verify_oauth2_token_sync",
        side_effect=GoogleAuthError("bad token"),
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer bad-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_invalid_bearer_format_returns_401() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/", headers={"Authorization": "Basic credentials"})
    assert response.status_code == 401


def test_verify_api_key_missing_raises_401() -> None:
    import pytest
    from fastapi import HTTPException

    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    try:
        auth.DISABLE_AUTH = False
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_api_key(None))
        assert exc_info.value.status_code == 401
    finally:
        auth.DISABLE_AUTH = original_disable


def test_verify_api_key_wrong_key_raises_401() -> None:

    import pytest
    from fastapi import HTTPException

    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    original_cfg = auth._auth_cfg
    try:
        auth.DISABLE_AUTH = False
        mock_cfg = MagicMock()
        mock_cfg.api_key = "correct-key"
        auth._auth_cfg = mock_cfg
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_api_key("wrong-key"))
        assert exc_info.value.status_code == 401
    finally:
        auth.DISABLE_AUTH = original_disable
        auth._auth_cfg = original_cfg


def test_verify_api_key_correct_key_returns_key() -> None:

    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    original_cfg = auth._auth_cfg
    try:
        auth.DISABLE_AUTH = False
        mock_cfg = MagicMock()
        mock_cfg.api_key = "valid-key"
        auth._auth_cfg = mock_cfg
        result = asyncio.run(auth.verify_api_key("valid-key"))
        assert result == "valid-key"
    finally:
        auth.DISABLE_AUTH = original_disable
        auth._auth_cfg = original_cfg


def test_verify_service_token_disabled_returns_dev_mode() -> None:
    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    try:
        auth.DISABLE_AUTH = True
        result = asyncio.run(auth.verify_service_token(None))
        assert result == "dev-mode"
    finally:
        auth.DISABLE_AUTH = original_disable


def test_verify_service_token_missing_raises_401() -> None:
    import pytest
    from fastapi import HTTPException

    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    try:
        auth.DISABLE_AUTH = False
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_service_token(None))
        assert exc_info.value.status_code == 401
    finally:
        auth.DISABLE_AUTH = original_disable
