"""Unit tests for invoice viewing, transitions, and analytics routes.

Covers:
  - invoices/viewing.py  (list_all_invoices, view_invoice_html, regenerate_all_invoices,
                           list/view charts, dashboards, monthly reports)
  - invoices/transitions.py (transition_invoice state machine)
  - invoices/analytics.py  (get_trade_analytics, get_aggregated_analytics,
                             get_performance_stats, _filter_orders_in_place, get_orders)

All tests run in mock mode.  Auth is injected via dependency_overrides.
"""

from __future__ import annotations

import functools
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes.invoices import analytics as _ana_mod
from client_reporting_api.api.routes.invoices import transitions as _trans_mod
from client_reporting_api.api.routes.invoices import viewing as _view_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------


def _make_internal_auth() -> AuthContext:
    return AuthContext(
        org_id="internal",
        user_id="admin",
        role="admin",
        subscription_tier="enterprise",
        display_name="Admin",
        is_internal=True,
    )


def _make_external_auth(client_id: str = "PR") -> AuthContext:
    return AuthContext(
        org_id=client_id,
        user_id=f"user-{client_id.lower()}",
        role="external",
        subscription_tier="pro",
        display_name=client_id,
        is_internal=False,
    )


@pytest.fixture(autouse=True)
def _disable_auth_globally() -> Generator[None]:
    """Patch UCI auth config so all token checks pass through."""
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    _uci_auth._get_auth_config.cache_clear()


@pytest.fixture
def _internal_auth() -> Generator[None]:
    async def _fake() -> AuthContext:
        return _make_internal_auth()

    app.dependency_overrides[_view_mod._require_auth] = _fake
    app.dependency_overrides[_trans_mod._require_auth] = _fake
    app.dependency_overrides[_ana_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_view_mod._require_auth, None)
    app.dependency_overrides.pop(_trans_mod._require_auth, None)
    app.dependency_overrides.pop(_ana_mod._require_auth, None)


@pytest.fixture
def _external_pr_auth() -> Generator[None]:
    async def _fake() -> AuthContext:
        return _make_external_auth("PR")

    app.dependency_overrides[_view_mod._require_auth] = _fake
    app.dependency_overrides[_ana_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_view_mod._require_auth, None)
    app.dependency_overrides.pop(_ana_mod._require_auth, None)


# ---------------------------------------------------------------------------
# Tests: invoices/viewing.py — _invoice_type_for (pure)
# ---------------------------------------------------------------------------


class TestInvoiceTypeFor:
    def test_trd_prefix_returns_trader(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import _invoice_type_for

        assert _invoice_type_for("TRD-001") == "trader"

    def test_int_prefix_returns_introducer(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import _invoice_type_for

        assert _invoice_type_for("INT-001") == "introducer"

    def test_other_prefix_returns_odum(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import _invoice_type_for

        assert _invoice_type_for("ODUM-2026-001") == "odum"

    def test_no_prefix_returns_odum(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import _invoice_type_for

        assert _invoice_type_for("INV-001") == "odum"


# ---------------------------------------------------------------------------
# Tests: invoices/viewing.py — list_all_invoices (GET /api/v1/invoices/all)
# ---------------------------------------------------------------------------


class TestListAllInvoices:
    """Tests for list_all_invoices.

    Note: GET /api/v1/invoices/all is shadowed by GET /{invoice_id} (route order bug,
    pre-existing).  Tests call the handler directly to cover the function body.
    """

    def test_internal_no_filter_returns_all(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_internal_auth(), client_id=None, invoice_type=None, status=None
        )
        assert isinstance(data, list)
        assert len(data) > 0
        for item in data:
            assert "invoice_id" in item
            assert "client_id" in item
            assert "type" in item
            assert "status" in item
            assert "total_due" in item

    def test_internal_filter_by_invoice_type_odum(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_internal_auth(), client_id=None, invoice_type="odum", status=None
        )
        for item in data:
            assert item["type"] == "odum"

    def test_internal_filter_by_invoice_type_trader(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_internal_auth(), client_id=None, invoice_type="trader", status=None
        )
        for item in data:
            assert item["type"] == "trader"

    def test_internal_filter_by_client_id(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_internal_auth(), client_id="PR", invoice_type=None, status=None
        )
        for item in data:
            assert item["client_id"] == "PR"

    def test_internal_filter_by_status_issued(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_internal_auth(), client_id=None, invoice_type=None, status="ISSUED"
        )
        for item in data:
            assert item["status"] == "ISSUED"

    def test_external_without_client_id_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        with pytest.raises(HTTPException) as exc_info:
            list_all_invoices(
                auth=_make_external_auth("PR"), client_id=None, invoice_type=None, status=None
            )
        assert exc_info.value.status_code == 403

    def test_external_with_own_client_id_returns_data(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        data = list_all_invoices(
            auth=_make_external_auth("PR"), client_id="PR", invoice_type=None, status=None
        )
        assert isinstance(data, list)
        for item in data:
            assert item["client_id"] == "PR"

    def test_external_with_other_client_id_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices.viewing import list_all_invoices

        with pytest.raises(HTTPException) as exc_info:
            list_all_invoices(
                auth=_make_external_auth("PR"), client_id="ET", invoice_type=None, status=None
            )
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/viewing.py — view_invoice_html (GET /api/v1/invoices/view/{id})
# ---------------------------------------------------------------------------


class TestViewInvoiceHtml:
    def test_invoice_found_returns_200(self, _internal_auth: None) -> None:
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write("<html><body>Test Invoice</body></html>")
            tmp_path = Path(f.name)

        with (
            patch(
                "client_reporting_api.api.routes.invoices.viewing.get_invoice_html_path",
                return_value=tmp_path,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/view/ODUM-2026-PR-Feb")
        assert response.status_code == 200
        assert "Test Invoice" in response.text
        tmp_path.unlink(missing_ok=True)

    def test_invoice_not_found_returns_404(self, _internal_auth: None) -> None:
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
            response = client.get("/api/v1/invoices/view/NONEXISTENT-INV-99")
        assert response.status_code == 404

    def test_external_caller_returns_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/view/ODUM-2026-PR-Feb")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/viewing.py — regenerate_all_invoices (POST /generate-all)
# ---------------------------------------------------------------------------


class TestRegenerateAllInvoices:
    def test_internal_triggers_generation(self, _internal_auth: None) -> None:
        fake_paths = ["/tmp/ODUM-2026-PR-Feb.html", "/tmp/ODUM-2026-NN-Feb.html"]
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_all_invoices",
            return_value=fake_paths,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post("/api/v1/invoices/generate-all")
        assert response.status_code == 200
        data = response.json()
        assert data["generated"] == 2

    def test_external_returns_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post("/api/v1/invoices/generate-all")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/viewing.py — list_charts / list_dashboards / list_monthly_reports
# ---------------------------------------------------------------------------


class TestListCharts:
    """GET /api/v1/invoices/charts is shadowed — tested via direct function call."""

    def test_empty_charts_dir_returns_empty_list(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_charts

        with patch("client_reporting_api.api.routes.invoices.viewing.generate_all_charts"):
            result = list_charts(auth=_make_internal_auth())
        assert isinstance(result, list)

    def test_external_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices.viewing import list_charts

        with pytest.raises(HTTPException) as exc_info:
            list_charts(auth=_make_external_auth("PR"))
        assert exc_info.value.status_code == 403


class TestListDashboards:
    """GET /api/v1/invoices/dashboards is shadowed — tested via direct function call."""

    def test_internal_returns_list(self) -> None:
        from client_reporting_api.api.routes.invoices.viewing import list_dashboards

        result = list_dashboards(auth=_make_internal_auth())
        assert isinstance(result, list)

    def test_external_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices.viewing import list_dashboards

        with pytest.raises(HTTPException) as exc_info:
            list_dashboards(auth=_make_external_auth("PR"))
        assert exc_info.value.status_code == 403


class TestListMonthlyReports:
    def test_external_own_client_returns_200_empty(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/reports/PR")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_external_other_client_returns_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/reports/ET")
        assert response.status_code == 403

    def test_internal_returns_200(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/reports/PR")
        assert response.status_code == 200


class TestViewPnlChart:
    def test_no_data_returns_404(self, _external_pr_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_pnl_chart",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/charts/PR")
        assert response.status_code == 404

    def test_chart_generated_returns_200(self, _internal_auth: None) -> None:
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write("<html><body>PnL Chart</body></html>")
            tmp_path = f.name

        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_pnl_chart",
            return_value=tmp_path,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/charts/PR")
        assert response.status_code == 200
        Path(tmp_path).unlink(missing_ok=True)


class TestViewDashboard:
    def test_no_data_returns_404(self, _external_pr_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_dashboard",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboards/PR")
        assert response.status_code == 404


class TestViewMonthlyReport:
    def test_no_data_returns_404(self, _external_pr_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_monthly_report",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/reports/PR/2026/3")
        assert response.status_code == 404

    def test_report_generated_returns_200(self, _internal_auth: None) -> None:
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
            f.write("<html><body>Monthly Report</body></html>")
            tmp_path = f.name

        with patch(
            "client_reporting_api.api.routes.invoices.viewing.generate_monthly_report",
            return_value=tmp_path,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/reports/PR/2026/3")
        assert response.status_code == 200
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: invoices/transitions.py — transition_invoice state machine
# ---------------------------------------------------------------------------


class TestTransitionInvoice:
    """Tests for transition_invoice state machine.

    Uses direct function calls for state-dependent cases to avoid MockStateStore
    seed/reset side effects between tests.
    """

    def test_404_when_invoice_not_found(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices._shared import InvoiceTransitionRequest
        from client_reporting_api.api.routes.invoices.transitions import transition_invoice

        with pytest.raises(HTTPException) as exc_info:
            transition_invoice(
                "NONEXISTENT-999",
                InvoiceTransitionRequest(action="issue"),
                auth=_make_internal_auth(),
            )
        assert exc_info.value.status_code == 404

    def test_400_unknown_action(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.put(
            "/api/v1/invoices/INV-2026-001/transition",
            json={"action": "teleport"},
        )
        assert response.status_code == 400
        assert "Unknown action" in response.json()["error"]["message"]

    def test_409_invalid_transition(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices._shared import InvoiceTransitionRequest
        from client_reporting_api.api.routes.invoices.transitions import transition_invoice

        accepted_inv = {"invoice_id": "FAKE-409", "status": "accepted", "client_id": "TEST"}

        with patch("client_reporting_api.api.routes.invoices.transitions.store") as mock_store:
            mock_store.get.return_value = accepted_inv.copy()

            with pytest.raises(HTTPException) as exc_info:
                transition_invoice(
                    "FAKE-409",
                    InvoiceTransitionRequest(action="dispute"),  # accepted → disputed not allowed
                    auth=_make_internal_auth(),
                )
        assert exc_info.value.status_code == 409

    def test_200_valid_transition_with_note(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import InvoiceTransitionRequest
        from client_reporting_api.api.routes.invoices.transitions import transition_invoice

        draft_inv = {"invoice_id": "FAKE-NOTE", "status": "draft", "client_id": "TEST"}

        with patch("client_reporting_api.api.routes.invoices.transitions.store") as mock_store:
            mock_store.get.return_value = draft_inv.copy()
            mock_store.update.return_value = None

            result = transition_invoice(
                "FAKE-NOTE",
                InvoiceTransitionRequest(action="issue", note="Now issued"),
                auth=_make_internal_auth(),
            )

        assert result["status"] == "issued"
        assert result["notes"] == "Now issued"

    def test_200_pay_with_txid(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import InvoiceTransitionRequest
        from client_reporting_api.api.routes.invoices.transitions import transition_invoice

        accepted_inv = {"invoice_id": "FAKE-PAY", "status": "accepted", "client_id": "TEST"}

        with patch("client_reporting_api.api.routes.invoices.transitions.store") as mock_store:
            mock_store.get.return_value = accepted_inv.copy()
            mock_store.update.return_value = None

            result = transition_invoice(
                "FAKE-PAY",
                InvoiceTransitionRequest(action="pay", payment_txid="0xabc123"),
                auth=_make_internal_auth(),
            )

        assert result["status"] == "paid"
        assert result["payment_txid"] == "0xabc123"

    def test_external_caller_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices._shared import InvoiceTransitionRequest
        from client_reporting_api.api.routes.invoices.transitions import transition_invoice

        with pytest.raises(HTTPException) as exc_info:
            transition_invoice(
                "INV-2026-001",
                InvoiceTransitionRequest(action="accept"),
                auth=_make_external_auth("PR"),
            )
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/analytics.py — _filter_orders_in_place (pure function)
# ---------------------------------------------------------------------------


class TestFilterOrdersInPlace:
    _ORDERS: ClassVar[list[dict[str, object]]] = [
        {"symbol": "BTCUSDT", "side": "buy", "status": "filled"},
        {"symbol": "ETHUSDT", "side": "sell", "status": "filled"},
        {"symbol": "BTCUSDT", "side": "sell", "status": "cancelled"},
    ]

    def test_no_filter_returns_all(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place(list(self._ORDERS), "", "", "")
        assert len(result) == 3

    def test_filter_by_symbol(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place(list(self._ORDERS), "BTC", "", "")
        assert len(result) == 2
        for o in result:
            assert "BTC" in str(o["symbol"])

    def test_filter_by_side(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place(list(self._ORDERS), "", "buy", "")
        assert len(result) == 1
        assert result[0]["side"] == "buy"

    def test_filter_by_status(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place(list(self._ORDERS), "", "", "cancelled")
        assert len(result) == 1
        assert result[0]["status"] == "cancelled"

    def test_combined_filters(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place(list(self._ORDERS), "BTC", "sell", "cancelled")
        assert len(result) == 1

    def test_empty_orders_returns_empty(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import _filter_orders_in_place

        result = _filter_orders_in_place([], "BTC", "buy", "filled")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: invoices/analytics.py — route handlers
# ---------------------------------------------------------------------------


class TestGetTradeAnalytics:
    def test_returns_200_with_mocked_data(self, _external_pr_auth: None) -> None:

        from client_reporting_api.core.trade_analytics import TradeAnalytics

        mock_analytics = TradeAnalytics(client_id="PR")

        with patch(
            "client_reporting_api.api.routes.invoices.analytics.compute_coin_breakdown",
            return_value=mock_analytics,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/analytics/PR")
        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "PR"

    def test_entitlement_blocks_other_client(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/analytics/ET")
        assert response.status_code == 403


class TestGetAggregatedAnalytics:
    """GET /api/v1/invoices/analytics is shadowed — tested via direct function call."""

    def test_internal_returns_dict(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import get_aggregated_analytics
        from client_reporting_api.core.trade_analytics import TradeAnalytics

        mock_analytics = TradeAnalytics(client_id="all")
        with patch(
            "client_reporting_api.api.routes.invoices.analytics.aggregate_clients",
            return_value=mock_analytics,
        ):
            result = get_aggregated_analytics(auth=_make_internal_auth(), client_ids="")
        assert result["client_id"] == "all"

    def test_internal_with_client_ids_filters_to_subset(self) -> None:
        from client_reporting_api.api.routes.invoices.analytics import get_aggregated_analytics
        from client_reporting_api.core.trade_analytics import TradeAnalytics

        captured: list[list[str]] = []

        def mock_aggregate(ids: list[str]) -> TradeAnalytics:
            captured.append(ids)
            return TradeAnalytics(client_id="subset")

        with patch(
            "client_reporting_api.api.routes.invoices.analytics.aggregate_clients",
            side_effect=mock_aggregate,
        ):
            get_aggregated_analytics(auth=_make_internal_auth(), client_ids="PR,ET")
        assert captured[0] == ["PR", "ET"]

    def test_external_raises_403(self) -> None:
        from fastapi import HTTPException

        from client_reporting_api.api.routes.invoices.analytics import get_aggregated_analytics

        with pytest.raises(HTTPException) as exc_info:
            get_aggregated_analytics(auth=_make_external_auth("PR"), client_ids="")
        assert exc_info.value.status_code == 403


class TestGetPerformanceStats:
    def test_no_equity_data_returns_404(self, _external_pr_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.invoices.analytics.get_equity_curve",
            return_value=[],
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/performance/PR")
        assert response.status_code == 404

    def test_with_equity_data_returns_200(self, _external_pr_auth: None) -> None:
        fake_curve = [
            {"date": "2026-01-01", "equity": 100000.0},
            {"date": "2026-01-02", "equity": 101000.0},
            {"date": "2026-01-03", "equity": 100500.0},
        ]
        fake_stats = {"sharpe": 1.5, "max_drawdown": -0.05, "win_rate": 0.6}
        with (
            patch(
                "client_reporting_api.api.routes.invoices.analytics.get_equity_curve",
                return_value=fake_curve,
            ),
            patch(
                "client_reporting_api.api.routes.invoices.analytics.compute_performance_stats",
                return_value=fake_stats,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/performance/PR")
        assert response.status_code == 200
        assert response.json()["sharpe"] == 1.5


class TestGetOrders:
    def test_no_orders_file_returns_empty(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/orders/PR")
        assert response.status_code == 200
        data = response.json()
        assert data["orders"] == []
        assert data["total"] == 0

    def test_pagination_params_accepted(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/invoices/orders/PR",
            params={"limit": 10, "offset": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 5

    def test_with_mock_orders_file(self, _internal_auth: None) -> None:
        import json

        orders_data = [
            {"symbol": "BTCUSDT", "side": "buy", "status": "filled", "timestamp": 1000},
            {"symbol": "ETHUSDT", "side": "sell", "status": "filled", "timestamp": 900},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            client_dir = Path(tmpdir) / "PR"
            client_dir.mkdir()
            orders_path = client_dir / "orders.json"
            orders_path.write_text(json.dumps(orders_data))

            with patch(
                "client_reporting_api.api.routes.invoices.analytics._BACKFILL_ROOT",
                Path(tmpdir),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/invoices/orders/PR")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["orders"]) == 2

    def test_symbol_filter_applied(self, _internal_auth: None) -> None:
        import json

        orders_data = [
            {"symbol": "BTCUSDT", "side": "buy", "status": "filled", "timestamp": 1000},
            {"symbol": "ETHUSDT", "side": "sell", "status": "filled", "timestamp": 900},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            client_dir = Path(tmpdir) / "PR"
            client_dir.mkdir()
            (client_dir / "orders.json").write_text(json.dumps(orders_data))

            with patch(
                "client_reporting_api.api.routes.invoices.analytics._BACKFILL_ROOT",
                Path(tmpdir),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get(
                    "/api/v1/invoices/orders/PR",
                    params={"symbol": "BTC"},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["orders"][0]["symbol"] == "BTCUSDT"

    def test_entitlement_blocks_other_client(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/orders/ET")
        assert response.status_code == 403
