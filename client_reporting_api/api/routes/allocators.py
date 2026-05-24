"""Allocator-facing reporting routes (Phase 4 — IM Pooled allocator views).

Exposes subscription history, redemption history, and cash-account ledger
for a single allocator / client. Entitlement is enforced strictly: the
caller's ``AuthContext.org_id`` (== entitled client id for external users)
must match the ``client_id`` path parameter. Internal callers (role
``admin`` / ``internal``, i.e. ``AuthContext.is_internal``) bypass the
check and can read any client's view — this matches the semantics of the
existing ``/api/v1/clients`` listing.

Data source for this phase is the in-memory ``FundAdminProvider``.
Wiring the real HTTP client (+ circuit breaker) to
``fund-administration-service`` is tracked as a Phase-5/6 follow-up in
``unified-trading-pm/plans/active/fund_administration_service_and_pooled_subscription_redemption_2026_04_20.md``.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_api_contracts import (
    AllocatorCashAccountView,
    AllocatorRedemption,
    AllocatorSubscription,
)
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.entitlement import _enforce_entitlement  # pyright: ignore[reportPrivateUsage]
from client_reporting_api.core.fund_admin_provider import (
    FundAdminProvider,
    get_fund_admin_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/allocators", tags=["allocators"])

# The allocator routes are scoped to a specific ``client_id`` and MUST be
# behind the standard auth gate. ``AuthContext`` is resolved once and then
# checked against the path param.
_require_auth = create_api_auth("client-reporting-api")

# Annotated FastAPI dependency aliases — keeps handler signatures clean
# and avoids B008 (function-call-in-default) while preserving the
# standard FastAPI dep-injection flow.
AuthDep = Annotated[AuthContext, Depends(_require_auth)]
ProviderDep = Annotated[FundAdminProvider, Depends(get_fund_admin_provider)]


@router.get("/{client_id}/subscriptions", response_model=list[AllocatorSubscription])
def list_allocator_subscriptions(
    client_id: str,
    auth: AuthDep,
    provider: ProviderDep,
) -> list[AllocatorSubscription]:
    """Return the full subscription history for an allocator / client.

    Ordering is the provider's insertion order; downstream callers should
    re-sort if a specific timeline is required (the UI renders via a
    table that owns its own sort).
    """
    _enforce_entitlement(auth, client_id)
    return provider.list_subscriptions_for_client(client_id)


@router.get("/{client_id}/redemptions", response_model=list[AllocatorRedemption])
def list_allocator_redemptions(
    client_id: str,
    auth: AuthDep,
    provider: ProviderDep,
) -> list[AllocatorRedemption]:
    """Return the full redemption history for an allocator / client."""
    _enforce_entitlement(auth, client_id)
    return provider.list_redemptions_for_client(client_id)


@router.get("/{client_id}/cash-account", response_model=AllocatorCashAccountView)
def get_allocator_cash_account(
    client_id: str,
    auth: AuthDep,
    provider: ProviderDep,
    share_class: Annotated[
        str,
        Query(
            description=(
                "Share-class key the view is scoped to. One cash-account exists per (client_id, share_class)."
            ),
        ),
    ] = "USDC",
) -> AllocatorCashAccountView:
    """Return the allocator's cash-account movements + current balance.

    Movements flatten subscriptions + redemptions chronologically;
    ``current_balance_usd`` is the signed sum of settled rows. YTD totals
    are unsigned gross figures for display.
    """
    _enforce_entitlement(auth, client_id)
    return provider.get_cash_account_view(client_id, share_class)


__all__ = ["router"]
