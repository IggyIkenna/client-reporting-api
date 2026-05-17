"""Unit tests for Phase 4 attribution routes (nav / pnl / positions / attribution).

Tests run in mock mode (CLOUD_MOCK_MODE=true) to avoid real bucket access.
Auth is disabled at the module level so requests reach route handlers.
Entitlement logic is tested via dependency_overrides (same pattern as allocators tests).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import attribution as _attr_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_mode() -> Generator[None]:
    """Enable mock mode and disable auth for all tests in this module."""
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True

    # Routes use UTL's create_api_auth which reads disable_auth via @lru_cache
    # of UnifiedCloudConfig — setting the module-level DISABLE_AUTH has no
    # effect on the route's auth dependency. Patch UTL's _get_auth_config
    # directly (2026-05-17, per client_reporting_api_coverage_below_floor).
    from unified_trading_library.cloud_interface import api_auth as _utl_api_auth  # noqa: qg-deep-import (test needs the @lru_cache _get_auth_config module attr — root facade only re-exports create_api_auth)

    _utl_api_auth._get_auth_config.cache_clear()
    _orig_utl_get_auth_config = _utl_api_auth._get_auth_config
    _utl_api_auth._get_auth_config = lambda: (True, False, None)  # type: ignore[misc,assignment]

    cfg = _attr_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _utl_api_auth._get_auth_config = _orig_utl_get_auth_config  # type: ignore[misc,assignment]
    _utl_api_auth._get_auth_config.cache_clear()
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


@pytest.fixture
def _client_a_auth() -> Generator[None]:
    """Override _require_auth to return external caller authenticated as client-A."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="client-A",
            user_id="user-a",
            role="external",
            subscription_tier="pro",
            display_name="Client A",
            is_internal=False,
        )

    app.dependency_overrides[_attr_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_attr_mod._require_auth, None)


@pytest.fixture
def _admin_auth() -> Generator[None]:
    """Override _require_auth to return internal admin caller."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_attr_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_attr_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/nav
# ---------------------------------------------------------------------------


class TestGetNav:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/nav")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "snapshots" in payload
        assert isinstance(payload["snapshots"], list)
        assert len(payload["snapshots"]) >= 1
        snapshot = payload["snapshots"][0]
        assert "date" in snapshot
        assert "nav_usd" in snapshot
        assert "nav_in_share_class" in snapshot

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/nav")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/nav")
        assert response.status_code == 200

    def test_date_range_params_accepted(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/clients/client-A/nav",
            params={"date_from": "2026-01-01", "date_to": "2026-05-01"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/pnl
# ---------------------------------------------------------------------------


class TestGetPnl:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/pnl")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "entries" in payload
        assert isinstance(payload["entries"], list)
        assert len(payload["entries"]) >= 1
        entry = payload["entries"][0]
        assert "period_tag" in entry
        assert "total_pnl" in entry
        assert "strategy_alpha_total" in entry
        assert "execution_alpha_total" in entry

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/pnl")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/pnl")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/positions
# ---------------------------------------------------------------------------


class TestGetPositions:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/positions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "positions" in payload
        assert isinstance(payload["positions"], list)
        assert len(payload["positions"]) >= 1
        pos = payload["positions"][0]
        assert "venue" in pos
        assert "instrument" in pos
        assert "qty" in pos
        assert "unrealized_pnl" in pos

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/positions")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/positions")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/attribution
# ---------------------------------------------------------------------------


class TestGetAttribution:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/attribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "rows" in payload
        assert isinstance(payload["rows"], list)
        assert len(payload["rows"]) >= 1
        row = payload["rows"][0]
        assert "strategy_id" in row
        assert "factor" in row
        assert "layer" in row
        assert "amount" in row

    def test_mock_has_strategy_and_execution_layers(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/attribution")
        assert response.status_code == 200
        rows = response.json()["rows"]
        layers = {row["layer"] for row in rows}
        assert "STRATEGY" in layers
        assert "EXECUTION" in layers

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/attribution")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/attribution")
        assert response.status_code == 200

    def test_date_range_params_accepted(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/clients/client-A/attribution",
            params={"date_from": "2026-01-01", "date_to": "2026-05-01"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Pure helper functions — _nav_from_rows / _pnl_from_rows / _attribution_from_rows
# ---------------------------------------------------------------------------

_SAMPLE_ROWS: list[dict[str, object]] = [
    {
        "timestamp": "2026-05-01T12:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "BTC-PERP",
        "factor": "CARRY",
        "layer": "STRATEGY",
        "amount": "300.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-1",
        "venue": "hyperliquid",
        "benchmark_price": "62000.00",
    },
    {
        "timestamp": "2026-05-01T14:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "ETH-PERP",
        "factor": "SLIPPAGE",
        "layer": "EXECUTION",
        "amount": "50.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-2",
        "venue": "binance",
        "benchmark_price": "3000.00",
    },
    {
        "timestamp": "2026-05-02T12:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "BTC-PERP",
        "factor": "CARRY",
        "layer": "STRATEGY",
        "amount": "150.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-3",
        "venue": "hyperliquid",
        "benchmark_price": "62500.00",
    },
]


class TestNavFromRows:
    def test_empty_rows_returns_empty_snapshots(self) -> None:
        result = _attr_mod._nav_from_rows("client-A", [], None, None)
        assert result["client_id"] == "client-A"
        assert result["share_class"] == "USDT"
        assert result["snapshots"] == []

    def test_aggregates_amounts_by_date(self) -> None:
        result = _attr_mod._nav_from_rows("client-A", _SAMPLE_ROWS, None, None)
        snapshots = result["snapshots"]
        assert isinstance(snapshots, list)
        assert len(snapshots) == 2  # two distinct dates: 2026-05-01 and 2026-05-02

        by_date = {s["date"]: s for s in snapshots}
        assert "2026-05-01" in by_date
        assert "2026-05-02" in by_date

        may1 = by_date["2026-05-01"]
        assert "nav_usd" in may1
        assert "nav_in_share_class" in may1
        assert "nav_delta_usd" in may1

    def test_handles_missing_timestamp(self) -> None:
        rows = [{"timestamp": None, "amount": "100.00"}]
        result = _attr_mod._nav_from_rows("client-X", rows, None, None)
        snapshots = result["snapshots"]
        assert len(snapshots) == 1
        assert snapshots[0]["date"] == "unknown"

    def test_handles_bad_amount_gracefully(self) -> None:
        rows = [{"timestamp": "2026-05-01T00:00:00+00:00", "amount": "not-a-number"}]
        result = _attr_mod._nav_from_rows("client-X", rows, None, None)
        snapshots = result["snapshots"]
        assert len(snapshots) == 0  # bad amount is suppressed via contextlib.suppress


class TestPnlFromRows:
    def test_empty_rows_returns_empty_entries(self) -> None:
        result = _attr_mod._pnl_from_rows("client-A", [])
        assert result["client_id"] == "client-A"
        assert result["share_class"] == "USDT"
        assert result["entries"] == []

    def test_aggregates_by_date(self) -> None:
        result = _attr_mod._pnl_from_rows("client-A", _SAMPLE_ROWS)
        entries = result["entries"]
        assert len(entries) == 2  # 2026-05-01 and 2026-05-02

    def test_strategy_and_execution_split(self) -> None:
        result = _attr_mod._pnl_from_rows("client-A", _SAMPLE_ROWS)
        entries = result["entries"]
        may1 = next(e for e in entries if e["period_tag"] == "2026-05-01")
        assert may1["strategy_alpha_total"] == "300.00"
        assert may1["execution_alpha_total"] == "50.00"
        assert may1["total_pnl"] == "350.00"

    def test_skips_invalid_amount(self) -> None:
        rows = [{"timestamp": "2026-05-01T00:00:00+00:00", "layer": "STRATEGY", "amount": "bad"}]
        result = _attr_mod._pnl_from_rows("client-X", rows)
        assert result["entries"] == []

    def test_handles_missing_timestamp(self) -> None:
        rows = [{"timestamp": None, "layer": "STRATEGY", "amount": "100.00"}]
        result = _attr_mod._pnl_from_rows("client-X", rows)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["period_tag"] == "unknown"


class TestAttributionFromRows:
    def test_empty_rows_returns_empty(self) -> None:
        result = _attr_mod._attribution_from_rows("client-A", [])
        assert result["client_id"] == "client-A"
        assert result["rows"] == []

    def test_projects_all_fields(self) -> None:
        result = _attr_mod._attribution_from_rows("client-A", _SAMPLE_ROWS)
        rows = result["rows"]
        assert len(rows) == 3
        row = rows[0]
        assert row["strategy_id"] == "carry_staked_basis"
        assert row["instrument_id"] == "BTC-PERP"
        assert row["date"] == "2026-05-01"
        assert row["factor"] == "CARRY"
        assert row["layer"] == "STRATEGY"
        assert row["amount"] == "300.00"
        assert row["archetype_id"] == "carry_staked_basis"
        assert row["fill_id"] == "fill-1"
        assert row["venue"] == "hyperliquid"
        assert row["benchmark_price"] == "62000.00"

    def test_none_timestamp_yields_none_date(self) -> None:
        rows = [{"timestamp": None, "strategy_id": "s1"}]
        result = _attr_mod._attribution_from_rows("client-X", rows)
        assert result["rows"][0]["date"] is None


# ---------------------------------------------------------------------------
# Live path — handlers with is_mock_mode()=False (mocked read_attribution_rows)
# ---------------------------------------------------------------------------


class TestLivePaths:
    def test_nav_live_path_calls_reader(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        orig_mock = cfg.cloud_mock_mode
        cfg.cloud_mock_mode = False  # type: ignore[misc]
        cfg.data_mode = "live"  # type: ignore[misc]
        try:
            with patch(
                "client_reporting_api.api.routes.attribution.read_attribution_rows",
                return_value=_SAMPLE_ROWS,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/nav")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["snapshots"]) > 0
        finally:
            cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
            cfg.data_mode = "mock"  # type: ignore[misc]

    def test_pnl_live_path_calls_reader(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        orig_mock = cfg.cloud_mock_mode
        cfg.cloud_mock_mode = False  # type: ignore[misc]
        cfg.data_mode = "live"  # type: ignore[misc]
        try:
            with patch(
                "client_reporting_api.api.routes.attribution.read_attribution_rows",
                return_value=_SAMPLE_ROWS,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/pnl")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["entries"]) > 0
        finally:
            cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
            cfg.data_mode = "mock"  # type: ignore[misc]

    def test_attribution_live_path_calls_reader(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        orig_mock = cfg.cloud_mock_mode
        cfg.cloud_mock_mode = False  # type: ignore[misc]
        cfg.data_mode = "live"  # type: ignore[misc]
        try:
            with patch(
                "client_reporting_api.api.routes.attribution.read_attribution_rows",
                return_value=_SAMPLE_ROWS,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/attribution")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["rows"]) > 0
        finally:
            cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
            cfg.data_mode = "mock"  # type: ignore[misc]
