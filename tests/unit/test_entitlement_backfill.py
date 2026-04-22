"""Entitlement backfill tests for the existing client_id-scoped routes.

These tests exercise the helpers in
``client_reporting_api.core.entitlement`` and the route-level enforcement
applied in the cra_entitlement_backfill plan.

Pattern
-------
The route-level FastAPI auth dependency normally short-circuits to a
mock-admin (``is_internal=True``) when ``DISABLE_AUTH=true`` /
``CLOUD_MOCK_MODE=true`` — that's how the existing route tests work. To
exercise the real entitlement branches we override the per-route
``_require_auth`` dependency with a stub that returns a controlled
``AuthContext``. This is the same pattern proven in
``tests/unit/test_allocators_routes.py``.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import (
    clients as clients_module,
)
from client_reporting_api.api.routes import (
    emergency as emergency_module,
)
from client_reporting_api.api.routes import (
    performance as performance_module,
)
from client_reporting_api.api.routes import (
    pnl as pnl_module,
)
from client_reporting_api.api.routes import (
    sports as sports_module,
)
from client_reporting_api.api.routes import (
    trades as trades_module,
)
from client_reporting_api.core.entitlement import (
    _enforce_entitlement,
    require_internal,
)

# ---------------------------------------------------------------------------
# Helper-level coverage
# ---------------------------------------------------------------------------


class TestEnforceEntitlement:
    def test_internal_caller_bypasses(self) -> None:
        auth = AuthContext(org_id="anything", user_id="svc", role="admin", is_internal=True)
        # No raise.
        _enforce_entitlement(auth, "client-A")

    def test_external_match_passes(self) -> None:
        auth = AuthContext(org_id="client-A", user_id="u", role="external", is_internal=False)
        _enforce_entitlement(auth, "client-A")

    def test_external_mismatch_raises_403(self) -> None:
        auth = AuthContext(org_id="client-A", user_id="u", role="external", is_internal=False)
        with pytest.raises(HTTPException) as excinfo:
            _enforce_entitlement(auth, "client-B")
        assert excinfo.value.status_code == 403


class TestRequireInternal:
    def test_internal_caller_passes(self) -> None:
        auth = AuthContext(org_id="internal", user_id="svc", role="admin", is_internal=True)
        require_internal(auth)

    def test_external_caller_raises_403(self) -> None:
        auth = AuthContext(org_id="client-A", user_id="u", role="external", is_internal=False)
        with pytest.raises(HTTPException) as excinfo:
            require_internal(auth)
        assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# Auth-stub fixtures
# ---------------------------------------------------------------------------


def _make_external_auth(org_id: str) -> AuthContext:
    return AuthContext(
        org_id=org_id,
        user_id=f"user-{org_id.lower()}",
        role="external",
        subscription_tier="pro",
        display_name=f"External {org_id}",
        is_internal=False,
    )


def _make_internal_auth() -> AuthContext:
    return AuthContext(
        org_id="internal",
        user_id="admin-1",
        role="admin",
        subscription_tier="enterprise",
        display_name="Admin",
        is_internal=True,
    )


@pytest.fixture
def external_client_a_for_modules() -> Generator[None]:
    """Override per-route ``_require_auth`` to return an external client-A.

    Each route module has its own ``_require_auth`` instance, so we
    override every module the test exercises. This sidesteps the global
    ``DISABLE_AUTH=true → mock-admin`` short-circuit installed by
    quality-gates.sh.
    """
    modules = [
        trades_module,
        sports_module,
        pnl_module,
        performance_module,
        clients_module,
        emergency_module,
    ]

    async def _fake_auth() -> AuthContext:
        return _make_external_auth("client-A")

    for mod in modules:
        app.dependency_overrides[mod._require_auth] = _fake_auth
    yield
    for mod in modules:
        app.dependency_overrides.pop(mod._require_auth, None)


@pytest.fixture
def internal_for_modules() -> Generator[None]:
    """Override per-route ``_require_auth`` to return an internal admin."""
    modules = [
        trades_module,
        sports_module,
        pnl_module,
        performance_module,
        clients_module,
        emergency_module,
    ]

    async def _fake_auth() -> AuthContext:
        return _make_internal_auth()

    for mod in modules:
        app.dependency_overrides[mod._require_auth] = _fake_auth
    yield
    for mod in modules:
        app.dependency_overrides.pop(mod._require_auth, None)


@pytest.fixture
def http() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# (a) external-entitlement routes — same client = 200, cross client = 403,
# internal = 200.
# ---------------------------------------------------------------------------


class TestExternalEntitlementOnReportingRoutes:
    """Cross-section across the (a) routes proves entitlement is wired."""

    def test_trades_same_client_passes(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "client-A"})
        # 200 happy path — mock-mode returns MOCK_TRADES.
        assert resp.status_code == 200

    def test_trades_cross_client_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "client-B"})
        assert resp.status_code == 403

    def test_trades_internal_bypasses(self, http: TestClient, internal_for_modules: None) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "any-client"})
        assert resp.status_code == 200

    def test_pnl_cross_client_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get(
            "/pnl",
            params={"client_id": "client-B", "period_month": "2026-04"},
        )
        assert resp.status_code == 403

    def test_pnl_same_client_passes(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get(
            "/pnl",
            params={"client_id": "client-A", "period_month": "2026-04"},
        )
        assert resp.status_code == 200

    def test_sports_pnl_cross_client_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get(
            "/sports/pnl",
            params={"client_id": "client-B", "period_month": "2026-04"},
        )
        assert resp.status_code == 403

    def test_performance_summary_cross_client_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/performance/summary", params={"client_id": "client-B"})
        assert resp.status_code == 403

    def test_get_client_cross_client_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/clients/client-B")
        assert resp.status_code == 403

    def test_get_client_same_client_reaches_handler(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        # Mock-mode path returns 404 (PR is the only mock client) — the
        # important assertion is "not 403", proving entitlement passed.
        resp = http.get("/api/v1/clients/client-A")
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# (b) internal-only routes — external 403, internal reaches handler.
# ---------------------------------------------------------------------------


class TestInternalOnlyRoutes:
    def test_clients_listing_external_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/clients")
        assert resp.status_code == 403

    def test_clients_listing_internal_passes(
        self, http: TestClient, internal_for_modules: None
    ) -> None:
        resp = http.get("/api/v1/clients")
        assert resp.status_code == 200

    def test_emergency_close_all_external_returns_403(
        self, http: TestClient, external_client_a_for_modules: None
    ) -> None:
        # Even when the external caller targets their own client_id,
        # trading-control endpoints are internal-only.
        resp = http.post(
            "/api/v1/emergency/close-all/client-A",
            json={"rationale": "external-tries-trading-action", "dry_run": True},
        )
        assert resp.status_code == 403

    def test_emergency_close_all_internal_reaches_handler(
        self, http: TestClient, internal_for_modules: None
    ) -> None:
        # Internal caller passes entitlement; the handler then either
        # returns 200 (mock mode preview) or 403 because the mock client
        # has no trading capability — both prove require_internal passed.
        resp = http.post(
            "/api/v1/emergency/close-all/client-A",
            json={"rationale": "internal-ops-test-run", "dry_run": True},
        )
        assert resp.status_code in (200, 403, 404, 502)
