"""Extended unit tests for client_reporting_api.auth covering missing branches."""

from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Production guard: DISABLE_AUTH=true in production is forbidden
# ---------------------------------------------------------------------------


def test_production_guard_disables_auth_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DISABLE_AUTH=true and environment=production, auth must remain ENABLED."""
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
        patch("unified_config_interface.UnifiedCloudConfig", return_value=mock_cfg),
        patch("unified_events_interface.setup_events"),
        patch("unified_events_interface.log_event"),
        patch("client_reporting_api._google_auth_sync.make_http_request", return_value=MagicMock()),
    ):
        import client_reporting_api.auth as auth_mod

    # Guard must have forced DISABLE_AUTH to False in production
    assert auth_mod.DISABLE_AUTH is False

    # Cleanup: remove the freshly-imported module so it doesn't pollute other tests
    del sys.modules["client_reporting_api.auth"]


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

    with patch(
        "client_reporting_api._google_auth_sync.id_token.verify_oauth2_token",
        side_effect=GoogleAuthError("invalid token"),
    ):
        with pytest.raises(GoogleAuthError):
            verify_oauth2_token_sync("bad-token", mock_http, "test-client-id")
