"""Unit tests for client_reporting_api.auth (GoogleOAuthMiddleware)."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from client_reporting_api.auth import GoogleOAuthMiddleware


def _make_app(client_id: str = "test-client", allowed_domains: list[str] | None = None) -> Starlette:
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
        client = TestClient(_make_app(allowed_domains=["allowed.com"]), raise_server_exceptions=False)
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


# ---------------------------------------------------------------------------
# Production guard: DISABLE_AUTH=true in production is forbidden
# ---------------------------------------------------------------------------


def test_production_guard_raises_on_disable_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DISABLE_AUTH=true and environment=production, service must raise RuntimeError."""
    mock_cfg = MagicMock()
    mock_cfg.disable_auth = True
    mock_cfg.environment = "production"
    mock_cfg.api_key = "secret"

    # Remove cached module so it re-evaluates the guard
    if "client_reporting_api.auth" in sys.modules:
        del sys.modules["client_reporting_api.auth"]
    if "client_reporting_api" in sys.modules:
        del sys.modules["client_reporting_api"]

    with (
        patch("client_reporting_api.config.get_config", return_value=mock_cfg),
        patch("unified_trading_library.config_interface.UnifiedCloudConfig", return_value=mock_cfg),
        patch("unified_trading_library.events.setup_events"),
        patch("unified_trading_library.events.log_event"),
        patch("client_reporting_api._google_auth_sync.make_http_request", return_value=MagicMock()),
        pytest.raises(RuntimeError, match="DISABLE_AUTH=true is forbidden in production"),
    ):
        import client_reporting_api.auth  # noqa: F401 — import triggers module-level production guard

    # Cleanup: remove the partially-imported module so it doesn't pollute other tests
    sys.modules.pop("client_reporting_api.auth", None)


# ---------------------------------------------------------------------------
# verify_service_token: invalid (wrong) token → 403
# ---------------------------------------------------------------------------


def test_verify_service_token_wrong_token_raises_403() -> None:
    """verify_service_token raises 403 when provided token doesn't match expected."""
    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    original_cfg = auth._auth_cfg
    try:
        auth.DISABLE_AUTH = False
        mock_cfg = MagicMock()
        mock_cfg.service_token = "correct-service-token"
        auth._auth_cfg = mock_cfg

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_service_token("wrong-service-token"))
        assert exc_info.value.status_code == 403
        assert "Invalid service token" in exc_info.value.detail
    finally:
        auth.DISABLE_AUTH = original_disable
        auth._auth_cfg = original_cfg


def test_verify_service_token_correct_token_returns_token() -> None:
    """verify_service_token returns the token when it matches expected."""
    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    original_cfg = auth._auth_cfg
    try:
        auth.DISABLE_AUTH = False
        mock_cfg = MagicMock()
        mock_cfg.service_token = "valid-service-token"
        auth._auth_cfg = mock_cfg

        result = asyncio.run(auth.verify_service_token("valid-service-token"))
        assert result == "valid-service-token"
    finally:
        auth.DISABLE_AUTH = original_disable
        auth._auth_cfg = original_cfg


def test_verify_service_token_no_expected_token_raises_403() -> None:
    """verify_service_token raises 403 when no expected token is configured."""
    from client_reporting_api import auth

    original_disable = auth.DISABLE_AUTH
    original_cfg = auth._auth_cfg
    try:
        auth.DISABLE_AUTH = False
        mock_cfg = MagicMock()
        # getattr fallback: service_token attribute doesn't exist → returns None
        mock_cfg.service_token = None
        auth._auth_cfg = mock_cfg

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_service_token("some-token"))
        assert exc_info.value.status_code == 403
    finally:
        auth.DISABLE_AUTH = original_disable
        auth._auth_cfg = original_cfg


# ---------------------------------------------------------------------------
# _google_auth_sync: verify_oauth2_token_sync execution path
# ---------------------------------------------------------------------------


def test_verify_oauth2_token_sync_calls_google_verify() -> None:
    """verify_oauth2_token_sync delegates to google id_token.verify_oauth2_token."""
    from client_reporting_api._google_auth_sync import verify_oauth2_token_sync

    fake_claims = {"sub": "user123", "email": "user@example.com"}
    mock_http = MagicMock()

    with patch(
        "client_reporting_api._google_auth_sync.id_token.verify_oauth2_token",
        return_value=fake_claims,
    ) as mock_verify:
        result = verify_oauth2_token_sync("fake-token", mock_http, "test-client-id")

    mock_verify.assert_called_once_with("fake-token", mock_http, "test-client-id")
    assert result == fake_claims


def test_verify_oauth2_token_sync_propagates_error() -> None:
    """verify_oauth2_token_sync propagates GoogleAuthError from google verify."""
    from google.auth.exceptions import GoogleAuthError

    from client_reporting_api._google_auth_sync import verify_oauth2_token_sync

    mock_http = MagicMock()

    with (
        patch(
            "client_reporting_api._google_auth_sync.id_token.verify_oauth2_token",
            side_effect=GoogleAuthError("invalid token"),
        ),
        pytest.raises(GoogleAuthError),
    ):
        verify_oauth2_token_sync("bad-token", mock_http, "test-client-id")
