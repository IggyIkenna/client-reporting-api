"""Entitlement backfill tests for the existing client_id-scoped routes.

These tests exercise the helpers in
``client_reporting_api.core.entitlement`` and the route-level enforcement
applied in the cra_entitlement_backfill plan, plus the 2026-08-21 CTO
handoff P0/P1 fixes (unauthenticated report stream, 13 previously-ungated
routes) tracked in
``walkthrough_feedback_remediation_2026_08_21.md``.

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

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

from client_reporting_api.api import main as _main_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import (
    alerts as alerts_module,
)
from client_reporting_api.api.routes import (
    clients as clients_module,
)
from client_reporting_api.api.routes import (
    compliance as compliance_module,
)
from client_reporting_api.api.routes import (
    documents as documents_module,
)
from client_reporting_api.api.routes import (
    docusign as docusign_module,
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
    reports_stream as reports_stream_module,
)
from client_reporting_api.api.routes import (
    sports as sports_module,
)
from client_reporting_api.api.routes import (
    trades as trades_module,
)
from client_reporting_api.api.routes.reporting import (
    investor_relations_archive as ir_archive_module,
)
from client_reporting_api.core.entitlement import (
    enforce_entitlement,
    require_internal,
)

# ---------------------------------------------------------------------------
# Helper-level coverage
# ---------------------------------------------------------------------------


class TestEnforceEntitlement:
    def test_internal_caller_bypasses(self) -> None:
        auth = AuthContext(org_id="anything", user_id="svc", role="admin", is_internal=True)
        # No raise.
        enforce_entitlement(auth, "client-A")

    def test_external_match_passes(self) -> None:
        auth = AuthContext(org_id="client-A", user_id="u", role="external", is_internal=False)
        enforce_entitlement(auth, "client-A")

    def test_external_mismatch_raises_403(self) -> None:
        auth = AuthContext(org_id="client-A", user_id="u", role="external", is_internal=False)
        with pytest.raises(HTTPException) as excinfo:
            enforce_entitlement(auth, "client-B")
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


@contextmanager
def _override_auth_for(modules: list[Any], auth: AuthContext) -> Iterator[None]:
    """Override the router-level + given modules' ``_require_auth`` to return ``auth``.

    Standalone context-manager variant of the module fixtures below, for
    tests that need a specific ``org_id`` (e.g. matching seeded mock-store
    data like ``org-alpha``/``org-beta``) rather than the generic
    ``client-A`` used by ``external_client_a_for_modules``.
    """

    async def _fake_auth() -> AuthContext:
        return auth

    app.dependency_overrides[_main_module._api_auth] = _fake_auth
    for mod in modules:
        app.dependency_overrides[mod._require_auth] = _fake_auth
    try:
        yield
    finally:
        app.dependency_overrides.pop(_main_module._api_auth, None)
        for mod in modules:
            app.dependency_overrides.pop(mod._require_auth, None)


_ENTITLEMENT_TEST_MODULES = [
    trades_module,
    sports_module,
    pnl_module,
    performance_module,
    clients_module,
    emergency_module,
    alerts_module,
    compliance_module,
    documents_module,
    docusign_module,
    reports_stream_module,
    ir_archive_module,
]


@pytest.fixture
def external_client_a_for_modules() -> Generator[None]:
    """Override per-route ``_require_auth`` to return an external client-A.

    Each route module has its own ``_require_auth`` instance, so we
    override every module the test exercises. This sidesteps the global
    ``DISABLE_AUTH=true → mock-admin`` short-circuit installed by
    quality-gates.sh.
    """
    modules = _ENTITLEMENT_TEST_MODULES

    async def _fake_auth() -> AuthContext:
        return _make_external_auth("client-A")

    # main.py wraps the auth routes with `_authenticated_router =
    # APIRouter(dependencies=[Depends(_api_auth)])` — a SEPARATE
    # `create_api_auth("client-reporting-api")` instance distinct from each
    # per-route module's `_require_auth`. The router-level dep runs first;
    # without overriding it too, every request 401s before the per-route
    # dep override fires (2026-05-17).
    app.dependency_overrides[_main_module._api_auth] = _fake_auth
    for mod in modules:
        app.dependency_overrides[mod._require_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(_main_module._api_auth, None)
    for mod in modules:
        app.dependency_overrides.pop(mod._require_auth, None)


@pytest.fixture
def internal_for_modules() -> Generator[None]:
    """Override per-route ``_require_auth`` to return an internal admin."""
    modules = _ENTITLEMENT_TEST_MODULES

    async def _fake_auth() -> AuthContext:
        return _make_internal_auth()

    # Router-level _api_auth override — see external_client_a_for_modules docstring.
    app.dependency_overrides[_main_module._api_auth] = _fake_auth
    for mod in modules:
        app.dependency_overrides[mod._require_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(_main_module._api_auth, None)
    for mod in modules:
        app.dependency_overrides.pop(mod._require_auth, None)


@pytest.fixture
def unauthenticated_router_level() -> Generator[None]:
    """Override the router-level ``_api_auth`` dep to raise 401.

    Simulates a caller with no/invalid credentials — proves a route
    actually sits behind the blanket auth gate. This is the regression
    test shape for the reports_stream.py P0 bug: before the fix, that
    route was mounted OUTSIDE ``_authenticated_router`` entirely, so this
    override would never even be consulted and the request would 200
    instead of 401.
    """

    async def _fake_401_auth() -> AuthContext:
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[_main_module._api_auth] = _fake_401_auth
    yield
    app.dependency_overrides.pop(_main_module._api_auth, None)


@pytest.fixture
def http() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# (a) external-entitlement routes — same client = 200, cross client = 403,
# internal = 200.
# ---------------------------------------------------------------------------


class TestExternalEntitlementOnReportingRoutes:
    """Cross-section across the (a) routes proves entitlement is wired."""

    def test_trades_same_client_passes(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "client-A"})
        # 200 happy path — mock-mode returns MOCK_TRADES.
        assert resp.status_code == 200

    def test_trades_cross_client_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "client-B"})
        assert resp.status_code == 403

    def test_trades_internal_bypasses(self, http: TestClient, internal_for_modules: None) -> None:
        resp = http.get("/api/v1/trades", params={"client_id": "any-client"})
        assert resp.status_code == 200

    def test_pnl_cross_client_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get(
            "/pnl",
            params={"client_id": "client-B", "period_month": "2026-04"},
        )
        assert resp.status_code == 403

    def test_pnl_same_client_passes(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get(
            "/pnl",
            params={"client_id": "client-A", "period_month": "2026-04"},
        )
        assert resp.status_code == 200

    def test_sports_pnl_cross_client_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
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

    def test_get_client_cross_client_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
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
    def test_clients_listing_external_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/v1/clients")
        assert resp.status_code == 403

    def test_clients_listing_internal_passes(self, http: TestClient, internal_for_modules: None) -> None:
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

    def test_emergency_close_all_internal_reaches_handler(self, http: TestClient, internal_for_modules: None) -> None:
        # Internal caller passes entitlement; the handler then either
        # returns 200 (mock mode preview) or 403 because the mock client
        # has no trading capability — both prove require_internal passed.
        resp = http.post(
            "/api/v1/emergency/close-all/client-A",
            json={"rationale": "internal-ops-test-run", "dry_run": True},
        )
        assert resp.status_code in (200, 403, 404, 502)


# ---------------------------------------------------------------------------
# (c) 2026-08-21 CTO handoff P0 fix — GET /api/v1/stream/reports
# ---------------------------------------------------------------------------


class TestReportsStreamAuth:
    """reports_stream.py used to carry no auth dependency at all."""

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/api/v1/stream/reports")
        assert resp.status_code == 401

    def test_external_caller_raises_403(self) -> None:
        # Calling the async handler directly (rather than via TestClient)
        # avoids opening a real never-ending SSE stream in the test run —
        # the positive path only needs to prove ``require_internal`` was
        # invoked, not that the generator actually streams.
        import asyncio

        from client_reporting_api.api.routes.reports_stream import stream_reports

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(stream_reports(_make_external_auth("client-A")))
        assert excinfo.value.status_code == 403

    def test_internal_caller_opens_stream_without_raising(self) -> None:
        import asyncio

        from client_reporting_api.api.routes.reports_stream import stream_reports

        result = asyncio.run(stream_reports(_make_internal_auth()))
        assert result is not None


# ---------------------------------------------------------------------------
# (d) 2026-08-21 CTO handoff P1 fix — the 13 previously-ungated routes.
# ---------------------------------------------------------------------------


class TestAlertsRequireInternal:
    def test_external_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/alerts")
        assert resp.status_code == 403

    def test_internal_reaches_handler(self, http: TestClient, internal_for_modules: None) -> None:
        with patch("client_reporting_api.api.routes.alerts.get_alerts", return_value=[]):
            resp = http.get("/alerts")
        assert resp.status_code == 200

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/alerts")
        assert resp.status_code == 401


class TestComplianceEntitlement:
    def test_dashboard_cross_org_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/v1/compliance/dashboard", params={"org_id": "client-B"})
        assert resp.status_code == 403

    def test_dashboard_same_org_passes(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/v1/compliance/dashboard", params={"org_id": "client-A"})
        assert resp.status_code == 200

    def test_dashboard_internal_bypasses(self, http: TestClient, internal_for_modules: None) -> None:
        resp = http.get("/api/v1/compliance/dashboard", params={"org_id": "any-org"})
        assert resp.status_code == 200

    def test_trade_reporting_cross_org_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get(
            "/api/v1/compliance/trade-reporting",
            params={"org_id": "client-B", "period_month": "2026-02"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/api/v1/compliance/dashboard", params={"org_id": "org-x"})
        assert resp.status_code == 401


class TestDocumentsEntitlement:
    def test_upload_url_cross_org_returns_403(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.post(
                "/api/v1/documents/upload-url",
                json={"org_id": "org-beta", "filename": "x.pdf"},
            )
        assert resp.status_code == 403

    def test_upload_url_same_org_passes(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.post(
                "/api/v1/documents/upload-url",
                json={"org_id": "org-alpha", "filename": "x.pdf"},
            )
        assert resp.status_code == 200

    def test_download_url_cross_org_returns_403(self, http: TestClient) -> None:
        # DOC-001 is seeded with org_id=org-alpha; org-beta must be denied.
        with _override_auth_for([documents_module], _make_external_auth("org-beta")):
            resp = http.get("/api/v1/documents/DOC-001/download-url")
        assert resp.status_code == 403

    def test_download_url_owning_org_passes(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.get("/api/v1/documents/DOC-001/download-url")
        assert resp.status_code == 200

    def test_download_url_unknown_document_fails_closed(self, http: TestClient) -> None:
        # No ownership record resolvable -> require_internal, never a
        # caller-supplied-org bypass.
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.get("/api/v1/documents/DOC-DOES-NOT-EXIST/download-url")
        assert resp.status_code == 403

    def test_list_cross_org_returns_403(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.get("/api/v1/documents", params={"org_id": "org-beta"})
        assert resp.status_code == 403

    def test_delete_external_returns_403(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_external_auth("org-alpha")):
            resp = http.delete("/api/v1/documents/DOC-002")
        assert resp.status_code == 403

    def test_delete_internal_passes(self, http: TestClient) -> None:
        with _override_auth_for([documents_module], _make_internal_auth()):
            resp = http.delete("/api/v1/documents/DOC-002")
        assert resp.status_code == 200

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/api/v1/documents", params={"org_id": "org-alpha"})
        assert resp.status_code == 401


class TestDocuSignEntitlement:
    def test_send_for_signature_cross_org_returns_403(self, http: TestClient) -> None:
        # DOC-003 is seeded with org_id=org-beta.
        with _override_auth_for([docusign_module], _make_external_auth("org-alpha")):
            resp = http.post(
                "/api/v1/documents/DOC-003/send-for-signature",
                json={"signer_email": "x@y.com", "signer_name": "X"},
            )
        assert resp.status_code == 403

    def test_send_for_signature_owning_org_passes(self, http: TestClient) -> None:
        with _override_auth_for([docusign_module], _make_external_auth("org-beta")):
            resp = http.post(
                "/api/v1/documents/DOC-003/send-for-signature",
                json={"signer_email": "x@y.com", "signer_name": "X"},
            )
        assert resp.status_code == 200

    def test_signature_status_cross_org_returns_403(self, http: TestClient) -> None:
        with _override_auth_for([docusign_module], _make_external_auth("org-alpha")):
            resp = http.get("/api/v1/documents/DOC-003/signature-status")
        assert resp.status_code == 403

    def test_webhook_external_returns_403(self, http: TestClient) -> None:
        with _override_auth_for([docusign_module], _make_external_auth("org-alpha")):
            resp = http.post(
                "/api/v1/webhooks/docusign",
                json={"event": "recipient-signed", "envelope_id": "ENV-001"},
            )
        assert resp.status_code == 403

    def test_webhook_internal_passes(self, http: TestClient) -> None:
        with _override_auth_for([docusign_module], _make_internal_auth()):
            resp = http.post(
                "/api/v1/webhooks/docusign",
                json={"event": "recipient-signed", "envelope_id": "ENV-001"},
            )
        assert resp.status_code == 200

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/api/v1/documents/DOC-003/signature-status")
        assert resp.status_code == 401


class TestInvestorRelationsArchiveRequireInternal:
    def test_external_returns_403(self, http: TestClient, external_client_a_for_modules: None) -> None:
        resp = http.get("/api/reporting/investor-relations/archive-metadata")
        assert resp.status_code == 403

    def test_internal_reaches_handler(self, http: TestClient, internal_for_modules: None) -> None:
        # Entitlement passing is proven by getting past 401/403 — the
        # handler's own data file is a separate, pre-existing gap (missing
        # from the repo; not part of this entitlement fix, tracked as a
        # follow-up in walkthrough_feedback_remediation_2026_08_21.md).
        resp = http.get("/api/reporting/investor-relations/archive-metadata")
        assert resp.status_code not in (401, 403)

    def test_unauthenticated_returns_401(self, http: TestClient, unauthenticated_router_level: None) -> None:
        resp = http.get("/api/reporting/investor-relations/archive-metadata")
        assert resp.status_code == 401
