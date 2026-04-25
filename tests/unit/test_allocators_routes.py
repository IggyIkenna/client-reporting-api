"""Unit tests for the allocator-facing reporting routes.

Covers:
- Happy path: subscriptions / redemptions / cash-account returning entries
  scoped to the requested client_id.
- Entitlement filter: external caller for client A cannot read client B
  (403).
- Internal caller (admin) can read any client.
- ``AllocatorCashAccountView`` schema roundtrip (returned payload matches
  the UAC type).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi.testclient import TestClient
from unified_api_contracts import (
    AllocatorCashAccountView,
    AllocatorRedemption,
    AllocatorSubscription,
    RedemptionStatus,
    SubscriptionStatus,
)
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import allocators as allocators_module
from client_reporting_api.core.fund_admin_provider import (
    InMemoryFundAdminProvider,
    set_fund_admin_provider,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_subscription(
    *,
    subscription_id: str,
    client_id: str,
    share_class: str = "USDC",
    amount_usd: str = "100000",
    status: SubscriptionStatus = SubscriptionStatus.SETTLED,
    timestamp: datetime | None = None,
) -> AllocatorSubscription:
    ts = timestamp or datetime(2026, 4, 20, 10, tzinfo=UTC)
    return AllocatorSubscription(
        subscription_id=subscription_id,
        fund_id="fund-alpha",
        allocator_id=client_id,
        share_class=share_class,
        requested_amount_usd=Decimal(amount_usd),
        requested_timestamp=ts,
        status=status,
        approval_timestamp=(ts + timedelta(hours=1))
        if status is SubscriptionStatus.SETTLED
        else None,
    )


def _make_redemption(
    *,
    redemption_id: str,
    client_id: str,
    share_class: str = "USDC",
    cash_usd: str = "50000",
    status: RedemptionStatus = RedemptionStatus.SETTLED,
    timestamp: datetime | None = None,
) -> AllocatorRedemption:
    ts = timestamp or datetime(2026, 4, 21, 10, tzinfo=UTC)
    return AllocatorRedemption(
        redemption_id=redemption_id,
        fund_id="fund-alpha",
        allocator_id=client_id,
        share_class=share_class,
        units_to_redeem=Decimal("500"),
        destination="0xabc",
        requested_timestamp=ts,
        status=status,
        grace_period_days=5,
        cash_amount_due_usd=Decimal(cash_usd) if status is RedemptionStatus.SETTLED else None,
        settlement_timestamp=(ts + timedelta(days=5))
        if status is RedemptionStatus.SETTLED
        else None,
    )


@pytest.fixture
def seeded_provider() -> InMemoryFundAdminProvider:
    """Provider seeded with data for two clients (A + B) across two share classes."""
    provider = InMemoryFundAdminProvider(
        subscriptions=[
            _make_subscription(
                subscription_id="sub-A-1",
                client_id="client-A",
                amount_usd="100000",
                timestamp=datetime(2026, 1, 15, 10, tzinfo=UTC),
            ),
            _make_subscription(
                subscription_id="sub-A-2",
                client_id="client-A",
                share_class="ETH",
                amount_usd="25000",
                timestamp=datetime(2026, 2, 10, 10, tzinfo=UTC),
            ),
            _make_subscription(
                subscription_id="sub-B-1",
                client_id="client-B",
                amount_usd="50000",
                timestamp=datetime(2026, 2, 1, 10, tzinfo=UTC),
            ),
        ],
        redemptions=[
            _make_redemption(
                redemption_id="red-A-1",
                client_id="client-A",
                cash_usd="30000",
                timestamp=datetime(2026, 3, 1, 10, tzinfo=UTC),
            ),
            _make_redemption(
                redemption_id="red-B-1",
                client_id="client-B",
                cash_usd="10000",
                status=RedemptionStatus.PENDING,
                timestamp=datetime(2026, 3, 5, 10, tzinfo=UTC),
            ),
        ],
    )
    return provider


@pytest.fixture(autouse=True)
def _wire_provider(
    seeded_provider: InMemoryFundAdminProvider,
) -> Generator[None]:
    set_fund_admin_provider(seeded_provider)
    yield
    set_fund_admin_provider(InMemoryFundAdminProvider())


@pytest.fixture
def _external_client_a_auth() -> Generator[None]:
    """Override the module-level auth dep so every request is authenticated as client-A.

    The route uses ``allocators_module._require_auth`` as a FastAPI
    dependency factory; FastAPI's ``dependency_overrides`` keys off the
    callable identity, so we override the exact object imported by the
    route module.
    """

    async def _fake_auth() -> AuthContext:
        return AuthContext(
            org_id="client-A",
            user_id="user-a-1",
            role="external",
            subscription_tier="pro",
            display_name="Client A",
            is_internal=False,
        )

    app.dependency_overrides[allocators_module._require_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(allocators_module._require_auth, None)


@pytest.fixture
def _internal_admin_auth() -> Generator[None]:
    async def _fake_auth() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin-1",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[allocators_module._require_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(allocators_module._require_auth, None)


# Note: ``CLOUD_MOCK_MODE=true`` (set by quality-gates.sh) makes UTL's
# ``create_api_auth`` short-circuit every request to a mock-admin. Tests
# in this module therefore override the module-level ``_require_auth``
# dependency directly via ``app.dependency_overrides`` — that replaces
# the dep wholesale and exercises the real entitlement logic without
# fighting the mock-mode early-return.

# ---------------------------------------------------------------------------
# GET /allocators/{client_id}/subscriptions
# ---------------------------------------------------------------------------


class TestListSubscriptions:
    def test_happy_path_returns_client_subscriptions(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-A/subscriptions")
        assert response.status_code == 200
        payload = cast(list[dict[str, object]], response.json())
        assert len(payload) == 2
        ids = {row["subscription_id"] for row in payload}
        assert ids == {"sub-A-1", "sub-A-2"}
        # Payload must pass UAC validation — every row is an AllocatorSubscription.
        parsed = [AllocatorSubscription.model_validate(row) for row in payload]
        assert all(sub.allocator_id == "client-A" for sub in parsed)

    def test_entitlement_denial_for_other_client(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-B/subscriptions")
        assert response.status_code == 403

    def test_internal_caller_can_read_any_client(self, _internal_admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-B/subscriptions")
        assert response.status_code == 200
        payload = cast(list[dict[str, object]], response.json())
        assert len(payload) == 1
        assert payload[0]["subscription_id"] == "sub-B-1"


# ---------------------------------------------------------------------------
# GET /allocators/{client_id}/redemptions
# ---------------------------------------------------------------------------


class TestListRedemptions:
    def test_happy_path_returns_client_redemptions(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-A/redemptions")
        assert response.status_code == 200
        payload = cast(list[dict[str, object]], response.json())
        assert len(payload) == 1
        parsed = AllocatorRedemption.model_validate(payload[0])
        assert parsed.redemption_id == "red-A-1"
        assert parsed.allocator_id == "client-A"

    def test_entitlement_denial_for_other_client(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-B/redemptions")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /allocators/{client_id}/cash-account
# ---------------------------------------------------------------------------


class TestCashAccountView:
    def test_happy_path_returns_view_with_movements(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-A/cash-account")
        assert response.status_code == 200
        view = AllocatorCashAccountView.model_validate(response.json())
        assert view.client_id == "client-A"
        assert view.share_class == "USDC"
        # Client A has one USDC SETTLED subscription for 100k and one
        # USDC SETTLED redemption for 30k ⇒ net 70k.
        assert view.current_balance_usd == Decimal("70000")
        assert view.subscriptions_ytd_usd == Decimal("100000")
        assert view.redemptions_ytd_usd == Decimal("30000")
        assert len(view.movements) == 2
        assert view.movements[0].movement_type == "SUBSCRIPTION"
        assert view.movements[1].movement_type == "REDEMPTION"
        assert view.last_settlement_timestamp is not None

    def test_share_class_filter_isolates_views(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/allocators/client-A/cash-account",
            params={"share_class": "ETH"},
        )
        assert response.status_code == 200
        view = AllocatorCashAccountView.model_validate(response.json())
        assert view.share_class == "ETH"
        assert len(view.movements) == 1
        assert view.movements[0].reference_id == "sub-A-2"
        assert view.current_balance_usd == Decimal("25000")

    def test_entitlement_denial_for_other_client(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-B/cash-account")
        assert response.status_code == 403

    def test_empty_view_for_unknown_share_class(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/allocators/client-A/cash-account",
            params={"share_class": "BTC"},
        )
        assert response.status_code == 200
        view = AllocatorCashAccountView.model_validate(response.json())
        assert len(view.movements) == 0
        assert view.current_balance_usd == Decimal("0")
        assert view.last_settlement_timestamp is None

    def test_schema_roundtrip_matches_uac_type(self, _external_client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/allocators/client-A/cash-account")
        assert response.status_code == 200
        # Reparsing via the UAC public facade must succeed — the route
        # contract matches the Pydantic shape exactly.
        view = AllocatorCashAccountView.model_validate(response.json())
        # Roundtrip: dump + re-validate yields an equal object.
        restored = AllocatorCashAccountView.model_validate(view.model_dump(mode="json"))
        assert restored == view


# ---------------------------------------------------------------------------
# Entitlement helper
# ---------------------------------------------------------------------------


class TestEntitlementEnforcement:
    """Directly exercises ``_enforce_entitlement`` to cover both branches.

    The route-level tests above cover the HTTP contract; this one locks
    the unit-level invariant so a future refactor of the helper doesn't
    drop the internal-bypass branch silently.
    """

    def test_external_mismatch_raises(self) -> None:
        from fastapi import HTTPException

        auth = AuthContext(
            org_id="client-A",
            user_id="u",
            role="external",
            is_internal=False,
        )
        with pytest.raises(HTTPException) as excinfo:
            allocators_module._enforce_entitlement(auth, "client-B")
        assert excinfo.value.status_code == 403

    def test_external_match_passes(self) -> None:
        auth = AuthContext(
            org_id="client-A",
            user_id="u",
            role="external",
            is_internal=False,
        )
        # No exception — None return is success.
        allocators_module._enforce_entitlement(auth, "client-A")

    def test_internal_bypass(self) -> None:
        auth = AuthContext(
            org_id="internal",
            user_id="svc",
            role="admin",
            is_internal=True,
        )
        allocators_module._enforce_entitlement(auth, "any-client")
