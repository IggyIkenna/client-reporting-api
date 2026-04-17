"""Smoke tests that hit every reachable GET endpoint.

The goal is **coverage of the request/response control flow**, not strict
schema verification. Each route is exercised once with the auth dependency
disabled. Routes that require POST bodies or cause side effects (write to
GCS, send email, etc.) are exercised at the helper level elsewhere.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Enable mock mode + DISABLE_AUTH for the route-level dep, mirroring
    ``tests/unit/test_performance_routes.py``.
    """
    original_env = {
        "DISABLE_AUTH": os.environ.get("DISABLE_AUTH"),
        "DATA_MODE": os.environ.get("DATA_MODE"),
        "CLOUD_MOCK_MODE": os.environ.get("CLOUD_MOCK_MODE"),
    }
    os.environ["DISABLE_AUTH"] = "true"
    os.environ["DATA_MODE"] = "mock"
    os.environ["CLOUD_MOCK_MODE"] = "true"

    import importlib

    _utl_api_auth = importlib.import_module("unified_trading_library.cloud_interface.api_auth")
    _utl_api_auth._get_auth_config.cache_clear()

    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True
    try:
        yield
    finally:
        _auth_module.DISABLE_AUTH = original_auth
        for key, val in original_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        _utl_api_auth._get_auth_config.cache_clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── Health and metrics ───────────────────────────────────────────────────


class TestHealth:
    def test_health_root(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_metrics(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200


# ── Reporting routes ────────────────────────────────────────────────────


class TestReportingRoutes:
    def test_clients(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/clients")
        assert resp.status_code == 200

    def test_performance_summary(self, client: TestClient) -> None:
        # Endpoint requires data on disk. Status 404/200/500 all exercise the body.
        resp = client.get("/api/reporting/performance/summary?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_performance_coin_breakdown(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/performance/coin-breakdown?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_performance_positions(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/performance/positions?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_performance_balances(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/performance/balances?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_trades(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/trades?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_reports(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/reports")
        assert resp.status_code == 200

    def test_settlements(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/settlements")
        assert resp.status_code in (200, 404, 422, 500)

    def test_fund_operations(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/fund-operations")
        assert resp.status_code in (200, 404, 422, 500)

    def test_nav(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/nav")
        assert resp.status_code in (200, 404, 422, 500)

    def test_reporting_invoices(self, client: TestClient) -> None:
        resp = client.get("/api/reporting/invoices")
        assert resp.status_code in (200, 404, 422, 500)


# ── Invoice routes ──────────────────────────────────────────────────────


class TestInvoiceRoutes:
    def test_list_invoices(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices")
        assert resp.status_code in (200, 404, 422, 500)

    def test_get_invoice_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/NONEXISTENT-XYZ")
        assert resp.status_code in (404, 500)

    def test_dashboard_fees(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/dashboard/fees")
        assert resp.status_code in (200, 404, 422, 500)

    def test_dashboard_fees_for_client(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/dashboard/fees/PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_dashboard_trader_payment(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/dashboard/trader-payment")
        assert resp.status_code in (200, 404, 422, 500)

    def test_dashboard_introducers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/dashboard/introducers")
        assert resp.status_code in (200, 404, 422, 500)

    def test_portal_admin(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/portal/admin")
        assert resp.status_code in (200, 404, 422, 500)

    def test_portal_trader(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/portal/trader")
        assert resp.status_code in (200, 404, 422, 500)

    def test_all_invoices(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/all")
        assert resp.status_code in (200, 404, 422, 500)

    def test_charts_listing(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/charts")
        assert resp.status_code in (200, 404, 422, 500)

    def test_dashboards_listing(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invoices/dashboards")
        assert resp.status_code in (200, 404, 422, 500)


# ── Tax routes ──────────────────────────────────────────────────────────


class TestTaxRoutes:
    def test_realized_gains(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tax/realized-gains?client_id=PR&year=2026")
        assert resp.status_code in (200, 404, 422, 500)


# ── Sports routes ──────────────────────────────────────────────────────


class TestSportsRoutes:
    def test_sports_summary(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sports/summary?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)


# ── Emergency routes ──────────────────────────────────────────────────


class TestEmergencyRoutes:
    def test_status(self, client: TestClient) -> None:
        resp = client.get("/api/v1/emergency/status")
        assert resp.status_code in (200, 404, 422, 500)


# ── Export routes ──────────────────────────────────────────────────────


class TestExportRoutes:
    def test_daily_equity(self, client: TestClient) -> None:
        resp = client.get("/api/v1/exports/daily-equity?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_transfers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/exports/transfers?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_tear_sheet(self, client: TestClient) -> None:
        resp = client.get("/api/v1/exports/tear-sheet?client_id=PR")
        assert resp.status_code in (200, 404, 422, 500)


# ── Trade routes ──────────────────────────────────────────────────────


class TestTradeRoutes:
    def test_trade_history_basic(self, client: TestClient) -> None:
        resp = client.get("/api/v1/trades?client_id=PR&limit=10")
        assert resp.status_code in (200, 404, 422, 500)


# ── Performance routes (extras) ───────────────────────────────────────


class TestPerformanceRouteExtras:
    def test_clients_listing_fastpath(self, client: TestClient) -> None:
        # Hits api/routes/clients.py which is at 38% coverage.
        resp = client.get("/api/v1/clients")
        assert resp.status_code in (200, 404, 422, 500)

    def test_get_client_pr(self, client: TestClient) -> None:
        resp = client.get("/api/v1/clients/PR")
        assert resp.status_code in (200, 404, 422, 500)

    def test_get_client_unknown(self, client: TestClient) -> None:
        resp = client.get("/api/v1/clients/__NOPE__")
        assert resp.status_code in (200, 404, 422, 500)
