"""Unit tests for client-list and performance dashboard routes.

Covers:
  - clients.py  (_build_client_entry, _filter_clients, _organisation_list,
                 _strategy_list, list_clients, get_client)
  - performance.py (get_performance_summary, get_open_positions,
                    get_balance_breakdown, get_coin_breakdown)

All tests run in mock mode (CLOUD_MOCK_MODE=true assumed by QG test env).
Auth is injected directly — no HTTP overhead for pure-function tests.
"""

from __future__ import annotations

import functools
from collections.abc import Generator

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import clients as _cli_mod
from client_reporting_api.api.routes import performance as _perf_mod

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
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    yield

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    _uci_auth._get_auth_config.cache_clear()


@pytest.fixture
def _internal_auth() -> Generator[None]:
    async def _fake() -> AuthContext:
        return _make_internal_auth()

    app.dependency_overrides[_cli_mod._require_auth] = _fake
    app.dependency_overrides[_perf_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_cli_mod._require_auth, None)
    app.dependency_overrides.pop(_perf_mod._require_auth, None)


@pytest.fixture
def _external_pr_auth() -> Generator[None]:
    async def _fake() -> AuthContext:
        return _make_external_auth("PR")

    app.dependency_overrides[_cli_mod._require_auth] = _fake
    app.dependency_overrides[_perf_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_cli_mod._require_auth, None)
    app.dependency_overrides.pop(_perf_mod._require_auth, None)


# ---------------------------------------------------------------------------
# Sample registry data for pure-function tests
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, object] = {
    "organisations": {
        "org-alpha": {"name": "Alpha Partners", "type": "client"},
        "org-beta": {"name": "Beta Capital", "type": "introducer"},
    },
    "strategies": {
        "strat-1": {"name": "Momentum", "description": "Trend-following"},
        "strat-2": {"name": "Arbitrage", "description": "Price dispersion"},
    },
    "clients": {
        "CLI-A": {
            "full_name": "Client Alpha",
            "venue": "okx",
            "currency": "USDT",
            "tranche": "managed",
            "is_active": True,
            "is_underwater": False,
            "organisation_id": "org-alpha",
            "strategy_id": "strat-1",
        },
        "CLI-B": {
            "full_name": "Client Beta",
            "venue": "binance",
            "currency": "USDT",
            "tranche": "growth",
            "is_active": False,
            "is_underwater": True,
            "organisation_id": "org-beta",
            "strategy_id": "strat-2",
        },
    },
}


# ---------------------------------------------------------------------------
# Tests: clients.py — _build_client_entry (pure)
# ---------------------------------------------------------------------------


class TestBuildClientEntry:
    def test_resolves_org_name_from_registry(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        cfg = _REGISTRY["clients"]["CLI-A"]
        assert isinstance(cfg, dict)
        entry = _build_client_entry("CLI-A", cfg, _REGISTRY)  # type: ignore[arg-type]
        assert entry["organisation_name"] == "Alpha Partners"
        assert entry["organisation_type"] == "client"

    def test_resolves_strategy_name_from_registry(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        cfg = _REGISTRY["clients"]["CLI-A"]
        assert isinstance(cfg, dict)
        entry = _build_client_entry("CLI-A", cfg, _REGISTRY)  # type: ignore[arg-type]
        assert entry["strategy_name"] == "Momentum"

    def test_unknown_org_falls_back_to_id(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        cfg = {"organisation_id": "org-unknown", "strategy_id": "strat-1"}
        entry = _build_client_entry("ANON", cfg, _REGISTRY)  # type: ignore[arg-type]
        assert entry["organisation_name"] == "org-unknown"
        assert entry["organisation_type"] == "client"

    def test_basic_fields_populated(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        cfg = _REGISTRY["clients"]["CLI-B"]
        assert isinstance(cfg, dict)
        entry = _build_client_entry("CLI-B", cfg, _REGISTRY)  # type: ignore[arg-type]
        assert entry["id"] == "CLI-B"
        assert entry["name"] == "Client Beta"
        assert entry["venue"] == "binance"
        assert entry["is_active"] is False
        assert entry["is_underwater"] is True

    def test_empty_cfg_uses_defaults(self) -> None:
        from client_reporting_api.api.routes.clients import _build_client_entry

        entry = _build_client_entry("EMPTY", {}, _REGISTRY)  # type: ignore[arg-type]
        assert entry["id"] == "EMPTY"
        assert entry["name"] == "EMPTY"
        assert entry["is_active"] is False


# ---------------------------------------------------------------------------
# Tests: clients.py — _filter_clients (pure)
# ---------------------------------------------------------------------------


class TestFilterClients:
    def test_no_filter_returns_all(self) -> None:
        from client_reporting_api.api.routes.clients import _filter_clients

        clients_cfg = _REGISTRY["clients"]
        assert isinstance(clients_cfg, dict)
        result = _filter_clients(clients_cfg, _REGISTRY, None, None)  # type: ignore[arg-type]
        assert len(result) == 2

    def test_filter_by_organisation_id(self) -> None:
        from client_reporting_api.api.routes.clients import _filter_clients

        clients_cfg = _REGISTRY["clients"]
        assert isinstance(clients_cfg, dict)
        result = _filter_clients(clients_cfg, _REGISTRY, "org-alpha", None)  # type: ignore[arg-type]
        assert len(result) == 1
        assert result[0]["id"] == "CLI-A"

    def test_filter_by_strategy_id(self) -> None:
        from client_reporting_api.api.routes.clients import _filter_clients

        clients_cfg = _REGISTRY["clients"]
        assert isinstance(clients_cfg, dict)
        result = _filter_clients(clients_cfg, _REGISTRY, None, "strat-2")  # type: ignore[arg-type]
        assert len(result) == 1
        assert result[0]["id"] == "CLI-B"

    def test_combined_filter_returns_empty_on_mismatch(self) -> None:
        from client_reporting_api.api.routes.clients import _filter_clients

        clients_cfg = _REGISTRY["clients"]
        assert isinstance(clients_cfg, dict)
        result = _filter_clients(clients_cfg, _REGISTRY, "org-alpha", "strat-2")  # type: ignore[arg-type]
        assert result == []

    def test_non_dict_cfg_skipped(self) -> None:
        from client_reporting_api.api.routes.clients import _filter_clients

        clients_cfg = {"BAD": "not-a-dict", "CLI-A": _REGISTRY["clients"]["CLI-A"]}
        result = _filter_clients(clients_cfg, _REGISTRY, None, None)  # type: ignore[arg-type]
        assert len(result) == 1
        assert result[0]["id"] == "CLI-A"


# ---------------------------------------------------------------------------
# Tests: clients.py — _organisation_list (pure)
# ---------------------------------------------------------------------------


class TestOrganisationList:
    def test_projects_org_entries(self) -> None:
        from client_reporting_api.api.routes.clients import _organisation_list

        result = _organisation_list(_REGISTRY)  # type: ignore[arg-type]
        ids = [o["id"] for o in result]
        assert "org-alpha" in ids
        assert "org-beta" in ids

    def test_empty_registry_returns_empty_list(self) -> None:
        from client_reporting_api.api.routes.clients import _organisation_list

        result = _organisation_list({})
        assert result == []

    def test_non_dict_orgs_returns_empty_list(self) -> None:
        from client_reporting_api.api.routes.clients import _organisation_list

        result = _organisation_list({"organisations": "not-a-dict"})
        assert result == []


# ---------------------------------------------------------------------------
# Tests: clients.py — _strategy_list (pure)
# ---------------------------------------------------------------------------


class TestStrategyList:
    def test_projects_strategy_entries(self) -> None:
        from client_reporting_api.api.routes.clients import _strategy_list

        result = _strategy_list(_REGISTRY)  # type: ignore[arg-type]
        ids = [s["id"] for s in result]
        assert "strat-1" in ids
        assert "strat-2" in ids

    def test_strategy_has_description_field(self) -> None:
        from client_reporting_api.api.routes.clients import _strategy_list

        result = _strategy_list(_REGISTRY)  # type: ignore[arg-type]
        by_id = {s["id"]: s for s in result}
        assert by_id["strat-1"]["description"] == "Trend-following"

    def test_empty_registry_returns_empty_list(self) -> None:
        from client_reporting_api.api.routes.clients import _strategy_list

        result = _strategy_list({})
        assert result == []


# ---------------------------------------------------------------------------
# Tests: clients.py — list_clients (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestListClients:
    def test_internal_caller_gets_mock_clients(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients")
        assert response.status_code == 200
        body = response.json()
        assert "clients" in body
        assert len(body["clients"]) > 0

    def test_external_caller_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients")
        assert response.status_code == 403

    def test_list_clients_direct_internal(self) -> None:
        from client_reporting_api.api.routes.clients import list_clients

        auth = _make_internal_auth()
        result = list_clients(auth=auth, organisation_id=None, strategy_id=None)
        assert "clients" in result
        assert isinstance(result["clients"], list)

    def test_list_clients_direct_external_raises_403(self) -> None:
        from client_reporting_api.api.routes.clients import list_clients

        auth = _make_external_auth("PR")
        with pytest.raises(HTTPException) as exc_info:
            list_clients(auth=auth, organisation_id=None, strategy_id=None)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: clients.py — get_client (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_internal_caller_gets_known_client(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients/PR")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "PR"

    def test_internal_caller_unknown_client_returns_404(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients/NONEXISTENT_XYZ")
        assert response.status_code == 404

    def test_external_caller_entitled_client(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients/PR")
        assert response.status_code == 200

    def test_external_caller_other_client_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/clients/ET")
        assert response.status_code == 403

    def test_get_client_direct_unknown_raises_404(self) -> None:
        from client_reporting_api.api.routes.clients import get_client

        auth = _make_internal_auth()
        with pytest.raises(HTTPException) as exc_info:
            get_client(client_id="NONEXISTENT_XYZ", auth=auth)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Tests: performance.py — get_performance_summary (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestGetPerformanceSummary:
    def test_internal_caller_gets_summary(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/summary?client_id=PR")
        assert response.status_code == 200
        body = response.json()
        assert "client_id" in body or "current_equity_usd" in body or "equity_curve" in body

    def test_external_caller_entitled_gets_summary(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/summary?client_id=PR")
        assert response.status_code == 200

    def test_external_caller_other_client_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/summary?client_id=ET")
        assert response.status_code == 403

    def test_direct_call_mock_mode_returns_dict(self) -> None:
        from client_reporting_api.api.routes.performance import get_performance_summary

        auth = _make_internal_auth()
        result = get_performance_summary(auth=auth, client_id="PR")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: performance.py — get_open_positions (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestGetOpenPositions:
    def test_internal_caller_gets_positions(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/positions?client_id=PR")
        assert response.status_code == 200
        body = response.json()
        assert "positions" in body

    def test_external_caller_entitled_gets_positions(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/positions?client_id=PR")
        assert response.status_code == 200

    def test_external_caller_other_client_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/positions?client_id=ET")
        assert response.status_code == 403

    def test_direct_call_returns_positions_key(self) -> None:
        from client_reporting_api.api.routes.performance import get_open_positions

        auth = _make_internal_auth()
        result = get_open_positions(auth=auth, client_id="PR")
        assert "positions" in result
        assert result["client_id"] == "PR"


# ---------------------------------------------------------------------------
# Tests: performance.py — get_balance_breakdown (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestGetBalanceBreakdown:
    def test_internal_caller_gets_balances(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/balances?client_id=PR")
        assert response.status_code == 200
        body = response.json()
        assert "balances" in body or "client_id" in body

    def test_external_caller_entitled_gets_balances(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/balances?client_id=PR")
        assert response.status_code == 200

    def test_external_caller_other_client_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/balances?client_id=ET")
        assert response.status_code == 403

    def test_direct_call_returns_client_id(self) -> None:
        from client_reporting_api.api.routes.performance import get_balance_breakdown

        auth = _make_internal_auth()
        result = get_balance_breakdown(auth=auth, client_id="PR")
        assert result["client_id"] == "PR"


# ---------------------------------------------------------------------------
# Tests: performance.py — get_coin_breakdown (HTTP, mock mode)
# ---------------------------------------------------------------------------


class TestGetCoinBreakdown:
    def test_internal_caller_gets_coins(self, _internal_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/coin-breakdown?client_id=PR")
        assert response.status_code == 200
        body = response.json()
        assert "coins" in body

    def test_external_caller_entitled_gets_coins(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/coin-breakdown?client_id=PR")
        assert response.status_code == 200

    def test_external_caller_other_client_gets_403(self, _external_pr_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/performance/coin-breakdown?client_id=ET")
        assert response.status_code == 403

    def test_direct_call_returns_coins_key(self) -> None:
        from client_reporting_api.api.routes.performance import get_coin_breakdown

        auth = _make_internal_auth()
        result = get_coin_breakdown(auth=auth, client_id="PR")
        assert "coins" in result
        assert result["client_id"] == "PR"


# ---------------------------------------------------------------------------
# Tests: performance.py — _decimal_to_float (pure)
# ---------------------------------------------------------------------------


class TestDecimalToFloat:
    def test_converts_decimal_to_float(self) -> None:
        from decimal import Decimal

        from client_reporting_api.api.routes.performance import _decimal_to_float

        assert _decimal_to_float(Decimal("1.5")) == 1.5

    def test_zero_decimal(self) -> None:
        from decimal import Decimal

        from client_reporting_api.api.routes.performance import _decimal_to_float

        assert _decimal_to_float(Decimal("0")) == 0.0
