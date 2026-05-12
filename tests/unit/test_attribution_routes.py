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

    cfg = _attr_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
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
