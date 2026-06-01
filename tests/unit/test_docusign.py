"""Unit tests for DocuSign integration routes (send, status, webhook)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and ensure mock mode is active for all DocuSign tests."""
    import functools

    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    from client_reporting_api.api.routes import docusign as _ds_mod

    cfg = _ds_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


class TestDocuSignRoutes:
    """Tests for DocuSign routes in mock mode."""

    def test_send_for_signature_creates_envelope(self) -> None:
        """POST /api/v1/documents/{doc_id}/send-for-signature creates an envelope."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/v1/documents/DOC-003/send-for-signature",
            json={
                "signer_email": "test@example.com",
                "signer_name": "Test User",
                "subject": "Please sign",
                "message": "Review and sign.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "envelope_id" in data
        assert data["envelope_id"].startswith("ENV-")
        assert data["document_id"] == "DOC-003"
        assert data["status"] == "sent"
        assert data["signer_email"] == "test@example.com"

    def test_signature_status_returns_status(self) -> None:
        """GET /api/v1/documents/{doc_id}/signature-status returns envelope status."""
        client = TestClient(app, raise_server_exceptions=True)
        # DOC-003 has a seeded envelope (ENV-001, status=completed)
        response = client.get("/api/v1/documents/DOC-003/signature-status")
        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == "DOC-003"
        assert "envelope_id" in data
        assert "status" in data
        assert "signer_email" in data
        assert "signer_name" in data

    def test_docusign_webhook_updates_status(self) -> None:
        """POST /api/v1/webhooks/docusign updates envelope status via webhook event."""
        client = TestClient(app, raise_server_exceptions=True)

        # First create an envelope so we have an envelope_id to target
        create_resp = client.post(
            "/api/v1/documents/DOC-001/send-for-signature",
            json={
                "signer_email": "webhook-test@example.com",
                "signer_name": "Webhook Tester",
            },
        )
        envelope_id = create_resp.json()["envelope_id"]

        # Send a webhook event to mark it as signed
        response = client.post(
            "/api/v1/webhooks/docusign",
            json={
                "event": "recipient-signed",
                "envelope_id": envelope_id,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "recipient-signed"

        # Verify the envelope status was updated
        status_resp = client.get("/api/v1/documents/DOC-001/signature-status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        # The most recent envelope for DOC-001 should now be signed
        assert status_data["status"] == "signed"
