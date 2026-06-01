"""Unit tests for invoice viewing routes (HTML views, charts, dashboards, reports).

Covers viewing.py which had 89 uncovered lines before this file was added.
"""

from __future__ import annotations

import functools
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import invoices as _inv_mod
from client_reporting_api.api.routes.invoices import viewing as _viewing_mod
from client_reporting_api.core.invoice_generator import InvoiceData

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _fake_invoice(
    invoice_id: str = "ODUM-2026-001",
    client_id: str = "PR",
    status: str = "ISSUED",
) -> InvoiceData:
    return InvoiceData(
        invoice_id=invoice_id,
        client_id=client_id,
        client_name="Test Client",
        invoice_date="2026-01-01",
        due_date="2026-01-31",
        currency="USD",
        previous_hwm=Decimal("100000"),
        deposits_since_hwm=Decimal("0"),
        withdrawals_since_hwm=Decimal("0"),
        effective_hwm=Decimal("100000"),
        current_balance=Decimal("105000"),
        profit=Decimal("5000"),
        fee_rate_pct=Decimal("20"),
        fee_label="Performance Fee (20%)",
        total_due=Decimal("1000"),
        line_items=[],
        payment_info={},
        status=status,
    )


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and set mock mode for all viewing route tests."""
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True

    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

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
    app.dependency_overrides.pop(_viewing_mod._require_auth, None)


@pytest.fixture
def _internal_viewing() -> Generator[None]:
    """Inject internal AuthContext for all viewing routes."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_viewing_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_viewing_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/view/{invoice_id}
# ---------------------------------------------------------------------------


class TestViewInvoiceHtml:
    def test_returns_404_when_invoice_not_found(self, _internal_viewing: None) -> None:
        """GET /view/{invoice_id} returns 404 when HTML file cannot be generated."""
        with (
            patch(
                "client_reporting_api.api.routes.invoices.viewing.get_invoice_html_path",
                return_value=None,
            ),
            patch(
                "client_reporting_api.api.routes.invoices.viewing.generate_all_invoices",
                return_value=[],
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/view/INV-NOTFOUND")
        assert response.status_code == 404

    def test_returns_html_when_invoice_found(self, _internal_viewing: None) -> None:
        """GET /view/{invoice_id} returns HTML content when file exists."""
        mock_path = MagicMock(spec=Path)
        mock_path.read_text.return_value = "<html><body>Invoice</body></html>"
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.get_invoice_html_path",
            return_value=mock_path,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/view/INV-2026-001")
        assert response.status_code == 200
        assert "Invoice" in response.text


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/all
# ---------------------------------------------------------------------------


class TestListAllInvoices:
    def test_returns_all_invoices_for_internal(self, _internal_viewing: None) -> None:
        """GET /all returns full invoice list for internal callers."""
        invoices = [
            _fake_invoice("ODUM-2026-001", "PR"),
            _fake_invoice("TRD-2026-001", "NN"),
            _fake_invoice("INT-2026-001", "ET"),
        ]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.get_all_2026_invoices",
            return_value=invoices,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/all")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        types = {inv["type"] for inv in data}
        assert types == {"odum", "trader", "introducer"}

    def test_filter_by_client_id(self, _internal_viewing: None) -> None:
        """GET /all?client_id=PR returns only PR invoices."""
        invoices = [
            _fake_invoice("ODUM-001", "PR"),
            _fake_invoice("ODUM-002", "NN"),
        ]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.get_all_2026_invoices",
            return_value=invoices,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/all", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["client_id"] == "PR"

    def test_filter_by_invoice_type(self, _internal_viewing: None) -> None:
        """GET /all?invoice_type=trader returns only trader invoices."""
        invoices = [
            _fake_invoice("ODUM-001", "PR"),
            _fake_invoice("TRD-001", "NN"),
        ]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.get_all_2026_invoices",
            return_value=invoices,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/all", params={"invoice_type": "trader"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["type"] == "trader"

    def test_filter_by_status(self, _internal_viewing: None) -> None:
        """GET /all?status=ISSUED returns only ISSUED invoices."""
        invoices = [
            _fake_invoice("ODUM-001", "PR", status="ISSUED"),
            _fake_invoice("ODUM-002", "NN", status="PAID"),
        ]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.get_all_2026_invoices",
            return_value=invoices,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/all", params={"status": "ISSUED"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "ISSUED"


# ---------------------------------------------------------------------------
# POST /api/v1/invoices/generate-all
# ---------------------------------------------------------------------------


class TestRegenerateAllInvoices:
    def test_regenerates_invoices_and_returns_count(self, _internal_viewing: None) -> None:
        """POST /generate-all regenerates all invoices and returns count."""
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_all_invoices",
            return_value=["data/INV-001.html", "data/INV-002.html"],
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post("/api/v1/invoices/generate-all")
        assert response.status_code == 200
        data = response.json()
        assert data["generated"] == 2
        assert "INV-001.html" in data["invoices"]


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/charts  and  GET /api/v1/invoices/charts/{client_id}
# ---------------------------------------------------------------------------


class TestCharts:
    def test_list_charts_returns_available_charts(self, _internal_viewing: None) -> None:
        """GET /charts lists charts when chart directory contains files."""
        mock_dir = MagicMock()
        mock_dir.exists.return_value = True
        mock_chart = MagicMock()
        mock_chart.exists.return_value = True
        mock_dir.__truediv__ = MagicMock(return_value=mock_chart)

        with patch.object(_viewing_mod, "_CHARTS_DIR", mock_dir):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/charts")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        assert "client_id" in data[0]
        assert "view_url" in data[0]

    def test_list_charts_generates_when_dir_missing(self, _internal_viewing: None) -> None:
        """GET /charts calls generate_all_charts when chart dir does not exist."""
        mock_dir = MagicMock()
        mock_dir.exists.return_value = False
        mock_chart = MagicMock()
        mock_chart.exists.return_value = False
        mock_dir.__truediv__ = MagicMock(return_value=mock_chart)

        with (
            patch.object(_viewing_mod, "_CHARTS_DIR", mock_dir),
            patch("client_reporting_api.api.routes.invoices.viewing.generate_all_charts") as mock_gen,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/charts")
        assert response.status_code == 200
        mock_gen.assert_called_once()
        assert response.json() == []


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/dashboards  and  POST /api/v1/invoices/dashboards/generate-all
# ---------------------------------------------------------------------------


class TestDashboards:
    def test_list_dashboards_returns_available(self, _internal_viewing: None) -> None:
        """GET /dashboards lists dashboards when dashboard files exist."""
        mock_dir = MagicMock()
        mock_dash = MagicMock()
        mock_dash.exists.return_value = True
        mock_dir.__truediv__ = MagicMock(return_value=mock_dash)

        with patch.object(_viewing_mod, "_DASH_DIR", mock_dir):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboards")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        assert "client_id" in data[0]
        assert "dashboard_url" in data[0]

    def test_list_dashboards_empty_when_no_files(self, _internal_viewing: None) -> None:
        """GET /dashboards returns empty list when no dashboard files exist."""
        mock_dir = MagicMock()
        mock_dash = MagicMock()
        mock_dash.exists.return_value = False
        mock_dir.__truediv__ = MagicMock(return_value=mock_dash)

        with patch.object(_viewing_mod, "_DASH_DIR", mock_dir):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboards")
        assert response.status_code == 200
        assert response.json() == []

    def test_regenerate_all_dashboards(self, _internal_viewing: None) -> None:
        """POST /dashboards/generate-all regenerates all dashboards."""
        mock_paths = [MagicMock(), MagicMock()]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_all_dashboards",
            return_value=mock_paths,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post("/api/v1/invoices/dashboards/generate-all")
        assert response.status_code == 200
        data = response.json()
        assert data["generated"] == 2


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/reports/{client_id}
# ---------------------------------------------------------------------------


class TestReports:
    def test_list_reports_returns_empty_when_dir_missing(self, _internal_viewing: None) -> None:
        """GET /reports/{client_id} returns empty list when reports dir does not exist."""
        mock_dir = MagicMock()
        mock_dir.exists.return_value = False

        with patch.object(_viewing_mod, "_REPORTS_DIR", mock_dir):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/reports/PR")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_reports_returns_urls_when_files_exist(self, _internal_viewing: None) -> None:
        """GET /reports/{client_id} returns report URLs when HTML files exist."""
        mock_report = MagicMock()
        mock_report.stem = "PR_2026-01_report"

        mock_dir = MagicMock()
        mock_dir.exists.return_value = True
        mock_dir.glob.return_value = [mock_report]

        with patch.object(_viewing_mod, "_REPORTS_DIR", mock_dir):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/reports/PR")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["month"] == "2026-01"
        assert "2026" in data[0]["report_url"]
