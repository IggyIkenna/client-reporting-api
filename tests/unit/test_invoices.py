"""Unit tests for invoice management routes (generate, list, detail, download)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import unified_cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and ensure mock mode is active for all invoice tests."""
    import functools

    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    from client_reporting_api.api.routes import invoices as _inv_mod

    cfg = _inv_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


class TestInvoiceRoutes:
    """Tests for /api/v1/invoices routes in mock mode."""

    def test_list_invoices_returns_mock_data(self) -> None:
        """GET /api/v1/invoices returns seeded invoices for the given org_id."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/invoices",
            params={"org_id": "org-alpha"},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for inv in data:
            assert inv["org_id"] == "org-alpha"

    def test_generate_invoice_creates_entry(self) -> None:
        """POST /api/v1/invoices/generate creates a new invoice record."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/v1/invoices/generate",
            json={
                "org_id": "org-test",
                "period_month": "2026-03",
                "invoice_type": "management_fee",
                "currency": "USD",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "invoice_id" in data
        assert data["invoice_id"].startswith("INV-")
        assert data["org_id"] == "org-test"
        assert data["status"] == "draft"
        assert data["type"] == "management_fee"
        assert data["subtotal"] > 0

    def test_get_invoice_by_id(self) -> None:
        """GET /api/v1/invoices/{invoice_id} returns the seeded invoice details."""
        client = TestClient(app, raise_server_exceptions=True)
        # INV-2026-001 is a seeded invoice
        response = client.get("/api/v1/invoices/INV-2026-001")
        assert response.status_code == 200
        data = response.json()
        assert data["invoice_id"] == "INV-2026-001"
        assert data["org_id"] == "org-alpha"
        assert data["type"] == "management_fee"

    def test_download_invoice_returns_url(self) -> None:
        """GET /api/v1/invoices/{invoice_id}/download returns a download URL."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/INV-2026-001/download")
        assert response.status_code == 200
        data = response.json()
        assert data["invoice_id"] == "INV-2026-001"
        assert "download_url" in data
        assert "mock-storage.example.com" in data["download_url"]
        assert data["expires_in_minutes"] == 60
        assert data["filename"] == "INV-2026-001.pdf"

    def test_get_nonexistent_invoice_returns_404(self) -> None:
        """GET /api/v1/invoices/{invoice_id} returns 404 for missing invoice."""
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/invoices/INV-NONEXISTENT")
        assert response.status_code == 404
