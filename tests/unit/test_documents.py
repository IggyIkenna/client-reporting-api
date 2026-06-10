"""Unit tests for document management routes (upload, download, list, delete)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient

from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and ensure mock mode is active for all tests."""
    _uci_auth._get_auth_config.cache_clear()
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    # Force auth disabled + mock mode
    import functools

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    from client_reporting_api.api.routes import documents as _docs_mod

    cfg = _docs_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


class TestDocumentRoutes:
    """Tests for /api/v1/documents routes in mock mode."""

    def test_upload_url_returns_url_and_id(self) -> None:
        """POST /api/v1/documents/upload-url returns document_id and upload_url."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/v1/documents/upload-url",
            json={
                "org_id": "org-test",
                "filename": "test-doc.pdf",
                "content_type": "application/pdf",
                "category": "REPORT",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "document_id" in data
        assert data["document_id"].startswith("DOC-")
        assert "upload_url" in data
        assert "mock-storage.example.com" in data["upload_url"]
        assert data["expires_in_minutes"] == 15

    def test_list_documents_returns_mock_data(self) -> None:
        """GET /api/v1/documents returns seeded documents for the given org_id."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/documents",
            params={"org_id": "org-alpha"},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # All returned documents should belong to org-alpha
        for doc in data:
            assert doc["org_id"] == "org-alpha"

    def test_list_documents_filter_by_category(self) -> None:
        """GET /api/v1/documents with category filter returns only matching docs."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/documents",
            params={"org_id": "org-alpha", "category": "INVOICE"},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        for doc in data:
            assert doc["category"] == "INVOICE"
            assert doc["org_id"] == "org-alpha"

    def test_download_url_returns_url(self) -> None:
        """GET /api/v1/documents/{document_id}/download-url returns a download URL."""
        client = TestClient(app, raise_server_exceptions=True)
        # DOC-001 is a seeded document
        response = client.get("/api/v1/documents/DOC-001/download-url")
        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == "DOC-001"
        assert "download_url" in data
        assert "mock-storage.example.com" in data["download_url"]
        assert data["expires_in_minutes"] == 60

    def test_delete_document_soft_deletes(self) -> None:
        """DELETE /api/v1/documents/{document_id} sets status to deleted."""
        client = TestClient(app, raise_server_exceptions=True)
        # First create a document via upload-url so we have a fresh doc to delete
        upload_resp = client.post(
            "/api/v1/documents/upload-url",
            json={
                "org_id": "org-test-delete",
                "filename": "to-delete.pdf",
                "category": "GENERAL",
            },
        )
        doc_id = upload_resp.json()["document_id"]

        # Now delete it
        response = client.delete(f"/api/v1/documents/{doc_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == doc_id
        assert data["status"] == "deleted"

    def test_download_nonexistent_returns_404(self) -> None:
        """GET /api/v1/documents/{document_id}/download-url returns 404 for missing doc."""
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/documents/DOC-NONEXISTENT/download-url")
        assert response.status_code == 404
