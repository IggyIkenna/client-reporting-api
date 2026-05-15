"""Unit tests for config_reloaders, invoices/generation.py, and exports.py.

Targets:
  - config_reloaders.py: callbacks + early-return path + stop (34 missing → ~22)
  - invoices/generation.py: pure helpers + live-mode paths (39 missing → ~35)
  - exports.py: tear sheet + daily equity CSV (33 missing → ~25)
  - clients.py live-mode paths (12 missing → ~11)
  - performance.py live-mode paths (additional)
"""

from __future__ import annotations

import functools
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import clients as _cli_mod
from client_reporting_api.api.routes import exports as _exp_mod
from client_reporting_api.api.routes.invoices import generation as _gen_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# Auth helpers
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

    app.dependency_overrides[_gen_mod._require_auth] = _fake
    app.dependency_overrides[_exp_mod._require_auth] = _fake
    app.dependency_overrides[_cli_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_gen_mod._require_auth, None)
    app.dependency_overrides.pop(_exp_mod._require_auth, None)
    app.dependency_overrides.pop(_cli_mod._require_auth, None)


@pytest.fixture
def _external_pr_auth() -> Generator[None]:
    async def _fake() -> AuthContext:
        return _make_external_auth("PR")

    app.dependency_overrides[_gen_mod._require_auth] = _fake
    app.dependency_overrides[_exp_mod._require_auth] = _fake
    app.dependency_overrides[_cli_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_gen_mod._require_auth, None)
    app.dependency_overrides.pop(_exp_mod._require_auth, None)
    app.dependency_overrides.pop(_cli_mod._require_auth, None)


# ---------------------------------------------------------------------------
# Tests: config_reloaders.py
# ---------------------------------------------------------------------------


class TestOnInstrumentsReload:
    def test_logs_and_emits_event(self) -> None:
        from client_reporting_api.config_reloaders import _on_instruments_reload

        mock_config = MagicMock()
        mock_config.subscription_list = ["BTC-USDT", "ETH-USDT"]
        mock_config.enabled_venues = ["okx", "binance"]

        with patch("client_reporting_api.config_reloaders.log_event") as mock_log:
            _on_instruments_reload(mock_config)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs[0][0] == "CONFIG_CHANGED"


class TestOnClientsReload:
    def test_logs_and_emits_event(self) -> None:
        from client_reporting_api.config_reloaders import _on_clients_reload

        mock_config = MagicMock()
        mock_config.active_clients = ["PR", "ET", "STD"]

        with patch("client_reporting_api.config_reloaders.log_event") as mock_log:
            _on_clients_reload(mock_config)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs[0][0] == "CONFIG_CHANGED"


class TestStartDomainConfigReloaders:
    def test_empty_bucket_returns_early(self) -> None:
        from client_reporting_api.config_reloaders import start_domain_config_reloaders

        mock_cfg = MagicMock()
        mock_cfg.config_store_bucket = ""
        mock_cfg.gcp_project_id = "test-project"

        with patch(
            "client_reporting_api.config_reloaders.DomainConfigReloader"
        ) as mock_reloader_cls:
            start_domain_config_reloaders(mock_cfg)

        mock_reloader_cls.assert_not_called()


class TestStopDomainConfigReloaders:
    def test_stop_with_none_reloaders_is_noop(self) -> None:
        import client_reporting_api.config_reloaders as _cr

        _cr._instrument_reloader = None
        _cr._client_reloader = None

        _cr.stop_domain_config_reloaders()

    def test_stop_with_active_reloaders_calls_stop(self) -> None:
        import client_reporting_api.config_reloaders as _cr

        mock_ir = MagicMock()
        mock_cr = MagicMock()
        _cr._instrument_reloader = mock_ir
        _cr._client_reloader = mock_cr

        _cr.stop_domain_config_reloaders()

        mock_ir.stop_watching.assert_called_once()
        mock_cr.stop_watching.assert_called_once()
        assert _cr._instrument_reloader is None
        assert _cr._client_reloader is None


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — _build_mock_invoice (pure)
# ---------------------------------------------------------------------------


class TestBuildMockInvoice:
    def test_management_fee_type(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import GenerateInvoiceRequest
        from client_reporting_api.api.routes.invoices.generation import _build_mock_invoice

        req = GenerateInvoiceRequest(
            org_id="org-alpha",
            period_month="2026-03",
            invoice_type="management_fee",
            currency="USD",
        )
        result = _build_mock_invoice(req, "INV-MGMT-001")
        assert result["type"] == "management_fee"
        assert result["status"] == "draft"
        assert result["id"] == "INV-MGMT-001"
        assert float(result["fee_rate_pct"]) == 2.0  # type: ignore[arg-type]

    def test_performance_fee_type_uses_else_branch(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import GenerateInvoiceRequest
        from client_reporting_api.api.routes.invoices.generation import _build_mock_invoice

        req = GenerateInvoiceRequest(
            org_id="org-beta",
            period_month="2026-03",
            invoice_type="performance_fee",
            currency="USD",
        )
        result = _build_mock_invoice(req, "INV-PERF-001")
        assert result["type"] == "performance_fee"
        assert float(result["fee_rate_pct"]) == 20.0  # type: ignore[arg-type]
        assert float(result["subtotal"]) > 0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — _build_live_invoice_line_items (pure)
# ---------------------------------------------------------------------------


class TestBuildLiveInvoiceLineItems:
    def test_empty_clients_returns_empty_items_and_zero_total(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import (
            _build_live_invoice_line_items,
        )

        items, total = _build_live_invoice_line_items([])
        assert items == []
        assert total == Decimal("0")

    def test_client_with_odum_fee_creates_line_item(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import (
            _build_live_invoice_line_items,
        )

        clients = [{"client_id": "PR", "odum_fee": Decimal("500.00"), "server_cost": Decimal("0")}]
        items, total = _build_live_invoice_line_items(clients)
        assert len(items) == 1
        assert items[0]["fee_type"] == "odum_fee"
        assert total == Decimal("500.00")

    def test_client_with_server_cost_creates_line_item(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import (
            _build_live_invoice_line_items,
        )

        clients = [{"client_id": "ET", "odum_fee": Decimal("0"), "server_cost": Decimal("150.00")}]
        items, total = _build_live_invoice_line_items(clients)
        assert len(items) == 1
        assert items[0]["fee_type"] == "server_cost"
        assert total == Decimal("150.00")

    def test_client_with_both_fees_creates_two_line_items(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import (
            _build_live_invoice_line_items,
        )

        clients = [
            {"client_id": "STD", "odum_fee": Decimal("300.00"), "server_cost": Decimal("75.00")}
        ]
        items, total = _build_live_invoice_line_items(clients)
        assert len(items) == 2
        assert total == Decimal("375.00")

    def test_non_decimal_fee_is_skipped(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import (
            _build_live_invoice_line_items,
        )

        clients = [{"client_id": "NN", "odum_fee": "not-a-decimal", "server_cost": 0}]
        items, total = _build_live_invoice_line_items(clients)
        assert items == []
        assert total == Decimal("0")


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — generate_invoice (mock mode HTTP)
# ---------------------------------------------------------------------------


class TestGenerateInvoice:
    def test_mock_mode_management_fee_returns_draft(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/invoices/generate",
            json={
                "org_id": "org-alpha",
                "period_month": "2026-03",
                "invoice_type": "management_fee",
                "currency": "USD",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "draft"
        assert "invoice_id" in body

    def test_mock_mode_performance_fee_returns_draft(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/invoices/generate",
            json={
                "org_id": "org-beta",
                "period_month": "2026-04",
                "invoice_type": "performance_fee",
                "currency": "USD",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "draft"

    def test_external_caller_returns_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/invoices/generate",
            json={
                "org_id": "org-alpha",
                "period_month": "2026-03",
                "invoice_type": "management_fee",
            },
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — list_invoices (mock mode, direct call)
# ---------------------------------------------------------------------------


class TestListInvoices:
    def test_internal_caller_lists_by_org_id(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import store
        from client_reporting_api.api.routes.invoices.generation import list_invoices

        store.seed("invoices", [])

        store.create(
            "invoices",
            {
                "id": "inv-list-1",
                "invoice_id": "inv-list-1",
                "org_id": "org-list-test",
                "status": "draft",
            },
        )

        auth = _make_internal_auth()
        result = list_invoices(auth=auth, org_id="org-list-test")
        assert any(inv.get("org_id") == "org-list-test" for inv in result)

    def test_external_caller_entitled_can_list(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import list_invoices

        auth = _make_external_auth("org-ext")
        result = list_invoices(auth=auth, org_id="org-ext")
        assert isinstance(result, list)

    def test_external_caller_other_org_raises_403(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import list_invoices

        auth = _make_external_auth("PR")
        with pytest.raises(HTTPException) as exc_info:
            list_invoices(auth=auth, org_id="other-org")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — get_invoice (mock mode, direct call)
# ---------------------------------------------------------------------------


class TestGetInvoice:
    def test_found_invoice_returns_dict(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import store
        from client_reporting_api.api.routes.invoices.generation import get_invoice

        store.seed("invoices", [])
        inv = store.create(
            "invoices",
            {
                "id": "inv-get-001",
                "invoice_id": "inv-get-001",
                "org_id": "org-alpha",
                "status": "draft",
            },
        )
        invoice_id = inv["id"]

        auth = _make_internal_auth()
        result = get_invoice(invoice_id=invoice_id, auth=auth)
        assert result["id"] == invoice_id

    def test_not_found_raises_404(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import get_invoice

        auth = _make_internal_auth()
        with pytest.raises(HTTPException) as exc_info:
            get_invoice(invoice_id="nonexistent-xyz-999", auth=auth)
        assert exc_info.value.status_code == 404

    def test_external_caller_raises_403(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import get_invoice

        auth = _make_external_auth("PR")
        with pytest.raises(HTTPException) as exc_info:
            get_invoice(invoice_id="any-invoice-id", auth=auth)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — download_invoice (mock mode, direct call)
# ---------------------------------------------------------------------------


class TestDownloadInvoice:
    def test_found_invoice_returns_download_url(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import store
        from client_reporting_api.api.routes.invoices.generation import download_invoice

        store.seed("invoices", [])
        inv = store.create(
            "invoices",
            {
                "id": "inv-dl-001",
                "invoice_id": "inv-dl-001",
                "org_id": "org-alpha",
                "status": "draft",
            },
        )
        invoice_id = inv["id"]

        auth = _make_internal_auth()
        result = download_invoice(invoice_id=invoice_id, auth=auth)
        assert "download_url" in result
        assert result["invoice_id"] == invoice_id

    def test_not_found_raises_404(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import download_invoice

        auth = _make_internal_auth()
        with pytest.raises(HTTPException) as exc_info:
            download_invoice(invoice_id="never-exists-xyz", auth=auth)
        assert exc_info.value.status_code == 404

    def test_external_caller_raises_403(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import download_invoice

        auth = _make_external_auth("PR")
        with pytest.raises(HTTPException) as exc_info:
            download_invoice(invoice_id="any-invoice-id", auth=auth)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: exports.py — _csv_stream (pure)
# ---------------------------------------------------------------------------


class TestCsvStream:
    def test_empty_rows_produces_header_only(self) -> None:
        from client_reporting_api.api.routes.exports import _csv_stream

        buf = _csv_stream([], ["col_a", "col_b"])
        content = buf.read()
        assert "col_a" in content
        assert "col_b" in content

    def test_rows_are_serialized(self) -> None:
        from client_reporting_api.api.routes.exports import _csv_stream

        rows = [{"col_a": "val1", "col_b": "val2"}]
        buf = _csv_stream(rows, ["col_a", "col_b"])
        content = buf.read()
        assert "val1" in content
        assert "val2" in content


# ---------------------------------------------------------------------------
# Tests: exports.py — HTTP routes (entitled external + internal)
# ---------------------------------------------------------------------------


class TestExportRoutes:
    def test_export_trades_csv_internal(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/trades?client_id=PR")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

    def test_export_daily_summary_csv_internal(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/daily-summary?client_id=PR")
        assert response.status_code == 200

    def test_export_hourly_snapshots_csv_internal(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/hourly-snapshots?client_id=PR")
        assert response.status_code == 200

    def test_export_coin_breakdown_csv_internal(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/coin-breakdown?client_id=PR")
        assert response.status_code == 200

    def test_export_transfers_csv_internal(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/transfers?client_id=PR")
        assert response.status_code == 200

    def test_export_external_entitled(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/trades?client_id=PR")
        assert response.status_code == 200

    def test_export_external_other_client_forbidden(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/trades?client_id=ET")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: exports.py — export_tear_sheet (internal only)
# ---------------------------------------------------------------------------


class TestExportTearSheet:
    def test_no_data_returns_404(self, _internal_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.exports.generate_tear_sheet",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/exports/tear-sheet?client_ids=PR&title=Test+Report")
        assert response.status_code == 404

    def test_with_html_path_returns_200(self, _internal_auth: None) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write("<html><body>Test Tear Sheet</body></html>")
            tmp_path = f.name

        try:
            with patch(
                "client_reporting_api.api.routes.exports.generate_tear_sheet",
                return_value=tmp_path,
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/api/v1/exports/tear-sheet?client_ids=PR&title=Test+Report")
            assert response.status_code == 200
            assert "Test Tear Sheet" in response.text
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_external_caller_returns_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/exports/tear-sheet?client_ids=PR")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: exports.py — export_daily_equity_csv (with equity curve data)
# ---------------------------------------------------------------------------


class TestExportDailyEquityCsv:
    def test_empty_curve_returns_no_data_response(self, _internal_auth: None) -> None:
        with patch(
            "client_reporting_api.api.routes.exports.get_equity_curve",
            return_value=[],
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/exports/daily-equity?client_id=PR")
        assert response.status_code == 200
        assert "No data" in response.text

    def test_with_curve_data_returns_csv(self, _internal_auth: None) -> None:
        fake_curve = [
            {"date": "2026-04-01", "equity_usd": 100000.0, "hwm_usd": 100000.0},
            {"date": "2026-04-02", "equity_usd": 102000.0, "hwm_usd": 102000.0},
            {"date": "2026-04-03", "equity_usd": 101000.0, "hwm_usd": 102000.0},
        ]
        with patch(
            "client_reporting_api.api.routes.exports.get_equity_curve",
            return_value=fake_curve,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/exports/daily-equity?client_id=PR")
        assert response.status_code == 200
        assert "2026-04-01" in response.text or "date" in response.text


# ---------------------------------------------------------------------------
# Tests: clients.py — live-mode paths (mocked load_registry + is_mock_mode=False)
# ---------------------------------------------------------------------------


class TestClientsLiveMode:
    def test_list_clients_live_mode_uses_registry(self) -> None:
        from client_reporting_api.api.routes.clients import list_clients

        fake_registry = {
            "clients": {
                "CLI-LIVE": {
                    "full_name": "Live Client",
                    "organisation_id": "org-1",
                    "strategy_id": "strat-1",
                    "venue": "okx",
                    "currency": "USDT",
                    "tranche": "managed",
                    "is_active": True,
                    "is_underwater": False,
                }
            },
            "organisations": {},
            "strategies": {},
        }
        mock_cfg = MagicMock()
        mock_cfg.is_mock_mode.return_value = False
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.clients._cloud_cfg", mock_cfg),
            patch(
                "client_reporting_api.api.routes.clients.load_registry", return_value=fake_registry
            ),
        ):
            result = list_clients(auth=auth, organisation_id=None, strategy_id=None)

        assert "clients" in result
        client_ids = [c["id"] for c in result["clients"]]  # type: ignore[union-attr]
        assert "CLI-LIVE" in client_ids

    def test_list_clients_live_mode_filter_by_org(self) -> None:
        from client_reporting_api.api.routes.clients import list_clients

        fake_registry = {
            "clients": {
                "CLI-ALPHA": {
                    "full_name": "Alpha Client",
                    "organisation_id": "org-alpha",
                    "strategy_id": "strat-1",
                    "venue": "okx",
                    "currency": "USDT",
                    "tranche": "managed",
                    "is_active": True,
                    "is_underwater": False,
                },
                "CLI-BETA": {
                    "full_name": "Beta Client",
                    "organisation_id": "org-beta",
                    "strategy_id": "strat-2",
                    "venue": "binance",
                    "currency": "USDT",
                    "tranche": "growth",
                    "is_active": True,
                    "is_underwater": False,
                },
            },
            "organisations": {},
            "strategies": {},
        }
        mock_cfg = MagicMock()
        mock_cfg.is_mock_mode.return_value = False
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.clients._cloud_cfg", mock_cfg),
            patch(
                "client_reporting_api.api.routes.clients.load_registry", return_value=fake_registry
            ),
        ):
            result = list_clients(auth=auth, organisation_id="org-alpha", strategy_id=None)

        client_ids = [c["id"] for c in result["clients"]]  # type: ignore[union-attr]
        assert "CLI-ALPHA" in client_ids
        assert "CLI-BETA" not in client_ids

    def test_get_client_live_mode_found(self) -> None:
        from client_reporting_api.api.routes.clients import get_client

        fake_registry = {
            "clients": {
                "CLI-LIVE-2": {
                    "full_name": "Live Client 2",
                    "organisation_id": "org-1",
                    "strategy_id": "strat-1",
                    "venue": "okx",
                    "currency": "USDT",
                    "tranche": "managed",
                    "is_active": True,
                    "is_underwater": False,
                }
            },
            "organisations": {},
            "strategies": {},
        }
        mock_cfg = MagicMock()
        mock_cfg.is_mock_mode.return_value = False
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.clients._cloud_cfg", mock_cfg),
            patch(
                "client_reporting_api.api.routes.clients.load_registry", return_value=fake_registry
            ),
        ):
            result = get_client(client_id="CLI-LIVE-2", auth=auth)

        assert result["id"] == "CLI-LIVE-2"

    def test_get_client_live_mode_not_found_raises_404(self) -> None:
        from client_reporting_api.api.routes.clients import get_client

        fake_registry = {"clients": {}, "organisations": {}, "strategies": {}}
        mock_cfg = MagicMock()
        mock_cfg.is_mock_mode.return_value = False
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.clients._cloud_cfg", mock_cfg),
            patch(
                "client_reporting_api.api.routes.clients.load_registry", return_value=fake_registry
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            get_client(client_id="NO-SUCH-CLIENT", auth=auth)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Tests: auth_standardized.py — import coverage (3 statements)
# ---------------------------------------------------------------------------


class TestAuthStandardized:
    def test_import_exposes_api_auth_and_auth_context(self) -> None:
        from client_reporting_api.auth_standardized import AuthContext, api_auth

        assert api_auth is not None
        assert AuthContext is not None


# ---------------------------------------------------------------------------
# Tests: config_reloaders.py — start with real bucket (lines 65-83)
# ---------------------------------------------------------------------------


class TestStartDomainConfigReloadersWithBucket:
    def test_non_empty_bucket_creates_two_reloaders(self) -> None:
        from client_reporting_api.config_reloaders import start_domain_config_reloaders

        mock_cfg = MagicMock()
        mock_cfg.config_store_bucket = "my-config-bucket"
        mock_cfg.gcp_project_id = "test-project"

        mock_reloader_instance = MagicMock()

        with patch(
            "client_reporting_api.config_reloaders.DomainConfigReloader",
            return_value=mock_reloader_instance,
        ) as mock_reloader_cls:
            start_domain_config_reloaders(mock_cfg)

        assert mock_reloader_cls.call_count == 2
        assert mock_reloader_instance.on_reload.call_count == 2
        assert mock_reloader_instance.start_watching.call_count == 2


# ---------------------------------------------------------------------------
# Tests: clients.py — edge cases for _build_client_entry non-dict org_info
# ---------------------------------------------------------------------------


class TestBuildClientEntryNonDictOrgInfo:
    def test_non_dict_org_info_falls_back_to_org_id(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        registry_with_bad_org = {
            "organisations": {"bad-org": "not-a-dict-value"},
            "strategies": {},
        }
        cfg = {"organisation_id": "bad-org", "strategy_id": ""}
        entry = _build_client_entry("CLIENT-X", cfg, registry_with_bad_org)  # type: ignore[arg-type]
        assert entry["organisation_name"] == "bad-org"
        assert entry["organisation_type"] == "client"


# ---------------------------------------------------------------------------
# Tests: clients.py — live mode non-dict clients_cfg (line 139)
# ---------------------------------------------------------------------------


class TestListClientsLiveModeNonDictClientsCfg:
    def test_non_dict_clients_cfg_returns_empty_clients(self) -> None:
        from client_reporting_api.api.routes.clients import list_clients

        fake_registry = {"clients": "not-a-dict", "organisations": {}, "strategies": {}}
        mock_cfg = MagicMock()
        mock_cfg.is_mock_mode.return_value = False
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.clients._cloud_cfg", mock_cfg),
            patch(
                "client_reporting_api.api.routes.clients.load_registry", return_value=fake_registry
            ),
        ):
            result = list_clients(auth=auth, organisation_id=None, strategy_id=None)

        assert result["clients"] == []


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — _clients_for_org (lines 71-75)
# ---------------------------------------------------------------------------


class TestClientsForOrg:
    def test_returns_clients_matching_org_id(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import _clients_for_org

        mock_summary = {
            "at_hwm": [{"client_id": "CLI-A"}],
            "underwater": [{"client_id": "CLI-B"}],
            "prop": [],
        }
        fake_registry = {
            "clients": {
                "CLI-A": {"organisation_id": "org-target"},
                "CLI-B": {"organisation_id": "org-other"},
            }
        }
        with (
            patch("client_reporting_api.api.routes.invoices.generation.state_mgr") as mock_state,
            patch(
                "client_reporting_api.api.routes.invoices.generation.load_registry",
                return_value=fake_registry,
            ),
        ):
            mock_state.get_dashboard_summary.return_value = mock_summary
            result = _clients_for_org("org-target")

        assert len(result) == 1
        assert result[0]["client_id"] == "CLI-A"


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — live mode list_invoices (lines 164-171)
# ---------------------------------------------------------------------------


class TestListInvoicesLiveMode:
    def test_live_mode_filters_by_org_client_ids(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import list_invoices

        fake_registry = {
            "clients": {
                "CLI-A": {"organisation_id": "org-live"},
                "CLI-B": {"organisation_id": "org-other"},
            }
        }
        fake_invoices = [
            {"invoice_id": "INV-001", "client_id": "CLI-A", "total": Decimal("500.00")},
            {"invoice_id": "INV-002", "client_id": "CLI-B", "total": Decimal("300.00")},
        ]
        mock_gen_cfg = MagicMock()
        mock_gen_cfg.is_mock_mode.return_value = False

        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.invoices.generation.cloud_cfg", mock_gen_cfg),
            patch(
                "client_reporting_api.api.routes.invoices.generation.load_registry",
                return_value=fake_registry,
            ),
            patch("client_reporting_api.api.routes.invoices.generation.state_mgr") as mock_state,
        ):
            mock_state.get_invoices.return_value = fake_invoices
            result = list_invoices(auth=auth, org_id="org-live")

        assert len(result) == 1
        assert result[0]["invoice_id"] == "INV-001"
        assert result[0]["total"] == 500.0


# ---------------------------------------------------------------------------
# Tests: clients.py — _strategy_list non-dict strategies (line 108)
# ---------------------------------------------------------------------------


class TestStrategyListNonDict:
    def test_non_dict_strategies_returns_empty_list(self) -> None:
        from client_reporting_api.api.routes.clients import _strategy_list

        result = _strategy_list({"strategies": "not-a-dict"})
        assert result == []


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — live mode generate_invoice (lines 126-137)
# ---------------------------------------------------------------------------


class TestGenerateInvoiceLiveMode:
    def test_live_mode_no_billable_clients_returns_draft_note(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import GenerateInvoiceRequest
        from client_reporting_api.api.routes.invoices.generation import generate_invoice

        mock_gen_cfg = MagicMock()
        mock_gen_cfg.is_mock_mode.return_value = False

        auth = _make_internal_auth()
        req = GenerateInvoiceRequest(
            org_id="org-empty",
            period_month="2026-05",
            invoice_type="management_fee",
            currency="USD",
        )
        with (
            patch("client_reporting_api.api.routes.invoices.generation.cloud_cfg", mock_gen_cfg),
            patch(
                "client_reporting_api.api.routes.invoices.generation._clients_for_org",
                return_value=[],
            ),
        ):
            result = generate_invoice(request=req, auth=auth)

        assert "invoice_id" in result
        assert result["status"] == "draft"
        assert "No billable clients" in str(result.get("note", ""))

    def test_live_mode_with_clients_builds_line_items(self) -> None:
        from client_reporting_api.api.routes.invoices._shared import GenerateInvoiceRequest
        from client_reporting_api.api.routes.invoices.generation import generate_invoice

        mock_gen_cfg = MagicMock()
        mock_gen_cfg.is_mock_mode.return_value = False

        fake_clients = [
            {"client_id": "CLI-A", "odum_fee": Decimal("300.00"), "server_cost": Decimal("0")}
        ]
        auth = _make_internal_auth()
        req = GenerateInvoiceRequest(
            org_id="org-alpha",
            period_month="2026-05",
            invoice_type="management_fee",
            currency="USD",
        )
        with (
            patch("client_reporting_api.api.routes.invoices.generation.cloud_cfg", mock_gen_cfg),
            patch(
                "client_reporting_api.api.routes.invoices.generation._clients_for_org",
                return_value=fake_clients,
            ),
        ):
            result = generate_invoice(request=req, auth=auth)

        assert "invoice_id" in result
        assert "line_items" in result
        assert result["total"] == 300.0


# ---------------------------------------------------------------------------
# Tests: invoices/generation.py — live mode get_invoice (lines 194-198)
# ---------------------------------------------------------------------------


class TestGetInvoiceLiveMode:
    def test_live_mode_found_invoice_returns_converted_dict(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import get_invoice

        mock_gen_cfg = MagicMock()
        mock_gen_cfg.is_mock_mode.return_value = False

        fake_invoices = [
            {"invoice_id": "INV-LIVE-001", "org_id": "org-alpha", "total": Decimal("750.00")}
        ]
        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.invoices.generation.cloud_cfg", mock_gen_cfg),
            patch("client_reporting_api.api.routes.invoices.generation.state_mgr") as mock_state,
        ):
            mock_state.get_invoices.return_value = fake_invoices
            result = get_invoice(invoice_id="INV-LIVE-001", auth=auth)

        assert result["invoice_id"] == "INV-LIVE-001"
        assert result["total"] == 750.0

    def test_live_mode_not_found_raises_404(self) -> None:
        from client_reporting_api.api.routes.invoices.generation import get_invoice

        mock_gen_cfg = MagicMock()
        mock_gen_cfg.is_mock_mode.return_value = False

        auth = _make_internal_auth()
        with (
            patch("client_reporting_api.api.routes.invoices.generation.cloud_cfg", mock_gen_cfg),
            patch("client_reporting_api.api.routes.invoices.generation.state_mgr") as mock_state,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_state.get_invoices.return_value = []
            get_invoice(invoice_id="NOT-FOUND-INVOICE", auth=auth)

        assert exc_info.value.status_code == 404
