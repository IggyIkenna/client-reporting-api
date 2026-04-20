"""Read-side access to fund-administration-service domain state.

Phase-4 (``fund_administration_service_and_pooled_subscription_redemption_2026_04_20``)
scope: client-reporting-api exposes allocator-facing subscription / redemption
/ cash-account views.  The data itself is owned by
``fund-administration-service`` — this file defines the **read-only** seam
client-reporting-api uses to talk to it.

**This phase is local-only.** No HTTP call to the sibling service, no
circuit breaker. An ``InMemoryFundAdminProvider`` satisfies the Protocol
with hand-seeded fixtures so route handlers can be exercised end-to-end in
tests and in ``CLOUD_MOCK_MODE=true`` local dev. Wiring the real
service-to-service client is a Phase-5/6 follow-up tracked in the same
plan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from unified_api_contracts import (
    AllocatorCashAccountView,
    AllocatorRedemption,
    AllocatorSubscription,
    CashAccountMovement,
    RedemptionStatus,
    SubscriptionStatus,
)


class FundAdminProvider(Protocol):
    """Read-only seam to fund-administration-service.

    Phase 4 keeps this local (in-memory / fixture-backed). Phase 5/6 will
    swap the default implementation for an HTTP-backed client with
    circuit-breaker + credential hot-reload, without changing route
    handlers.
    """

    def list_subscriptions_for_client(self, client_id: str) -> list[AllocatorSubscription]: ...

    def list_redemptions_for_client(self, client_id: str) -> list[AllocatorRedemption]: ...

    def get_cash_account_view(
        self, client_id: str, share_class: str
    ) -> AllocatorCashAccountView: ...


# ---------------------------------------------------------------------------
# In-memory implementation (mock / fixture data source for Phase 4)
# ---------------------------------------------------------------------------


def _amount_usd_for_subscription(sub: AllocatorSubscription) -> Decimal:
    """Signed cash amount for a subscription row (positive = inflow)."""
    return sub.requested_amount_usd


def _amount_usd_for_redemption(red: AllocatorRedemption) -> Decimal:
    """Signed cash amount for a redemption row (negative = outflow).

    We prefer ``cash_amount_due_usd`` once resolved at grace-period expiry;
    pre-resolution we fall back to ``0`` so the ledger stays balanced.
    """
    if red.cash_amount_due_usd is None:
        return Decimal("0")
    return -red.cash_amount_due_usd


def _sub_timestamp(sub: AllocatorSubscription) -> datetime:
    """Best-available timestamp for a subscription row."""
    return sub.approval_timestamp or sub.requested_timestamp


def _red_timestamp(red: AllocatorRedemption) -> datetime:
    """Best-available timestamp for a redemption row."""
    return red.settlement_timestamp or red.requested_timestamp


def _ytd_filter(ts: datetime, now: datetime) -> bool:
    return ts.year == now.year


def _build_movements(
    subs: list[AllocatorSubscription],
    reds: list[AllocatorRedemption],
) -> list[CashAccountMovement]:
    """Flatten subscriptions + redemptions into a chronologically sorted ledger."""
    rows: list[CashAccountMovement] = [
        CashAccountMovement(
            reference_id=sub.subscription_id,
            movement_type="SUBSCRIPTION",
            status=sub.status.value,
            amount_usd=_amount_usd_for_subscription(sub),
            timestamp=_sub_timestamp(sub),
        )
        for sub in subs
    ]
    rows.extend(
        CashAccountMovement(
            reference_id=red.redemption_id,
            movement_type="REDEMPTION",
            status=red.status.value,
            amount_usd=_amount_usd_for_redemption(red),
            timestamp=_red_timestamp(red),
        )
        for red in reds
    )
    rows.sort(key=lambda m: m.timestamp)
    return rows


def _aggregate_balances(
    subs: list[AllocatorSubscription],
    reds: list[AllocatorRedemption],
    now: datetime,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return ``(settled_balance, subscriptions_ytd, redemptions_ytd)``.

    All three are derived from SETTLED rows only — pending / pre-settlement
    movements never move cash.
    """
    settled_balance = sum(
        (_amount_usd_for_subscription(s) for s in subs if s.status is SubscriptionStatus.SETTLED),
        Decimal("0"),
    ) + sum(
        (_amount_usd_for_redemption(r) for r in reds if r.status is RedemptionStatus.SETTLED),
        Decimal("0"),
    )
    subscriptions_ytd = sum(
        (
            s.requested_amount_usd
            for s in subs
            if s.status is SubscriptionStatus.SETTLED and _ytd_filter(_sub_timestamp(s), now)
        ),
        Decimal("0"),
    )
    redemptions_ytd = sum(
        (
            r.cash_amount_due_usd
            for r in reds
            if r.status is RedemptionStatus.SETTLED
            and r.cash_amount_due_usd is not None
            and _ytd_filter(_red_timestamp(r), now)
        ),
        Decimal("0"),
    )
    return settled_balance, subscriptions_ytd, redemptions_ytd


def _last_settlement_timestamp(
    subs: list[AllocatorSubscription], reds: list[AllocatorRedemption]
) -> datetime | None:
    """Latest settled-movement timestamp across subscriptions + redemptions."""
    candidates: list[datetime] = [
        s.approval_timestamp
        for s in subs
        if s.status is SubscriptionStatus.SETTLED and s.approval_timestamp is not None
    ]
    candidates.extend(
        r.settlement_timestamp
        for r in reds
        if r.status is RedemptionStatus.SETTLED and r.settlement_timestamp is not None
    )
    return max(candidates) if candidates else None


class InMemoryFundAdminProvider:
    """In-memory ``FundAdminProvider`` backed by fixture data.

    Used in tests and local dev. Replace with the HTTP client once the
    service-to-service transport lands.
    """

    def __init__(
        self,
        subscriptions: list[AllocatorSubscription] | None = None,
        redemptions: list[AllocatorRedemption] | None = None,
    ) -> None:
        self._subscriptions: list[AllocatorSubscription] = list(subscriptions or [])
        self._redemptions: list[AllocatorRedemption] = list(redemptions or [])

    # ----- Mutators (fixture seeding / tests) ---------------------------
    def add_subscription(self, sub: AllocatorSubscription) -> None:
        self._subscriptions.append(sub)

    def add_redemption(self, red: AllocatorRedemption) -> None:
        self._redemptions.append(red)

    # ----- Protocol implementation --------------------------------------
    def list_subscriptions_for_client(self, client_id: str) -> list[AllocatorSubscription]:
        return [s for s in self._subscriptions if s.allocator_id == client_id]

    def list_redemptions_for_client(self, client_id: str) -> list[AllocatorRedemption]:
        return [r for r in self._redemptions if r.allocator_id == client_id]

    def get_cash_account_view(self, client_id: str, share_class: str) -> AllocatorCashAccountView:
        subs = [
            s
            for s in self._subscriptions
            if s.allocator_id == client_id and s.share_class == share_class
        ]
        reds = [
            r
            for r in self._redemptions
            if r.allocator_id == client_id and r.share_class == share_class
        ]
        now = datetime.now(UTC)
        settled_balance, subscriptions_ytd, redemptions_ytd = _aggregate_balances(subs, reds, now)
        return AllocatorCashAccountView(
            client_id=client_id,
            share_class=share_class,
            current_balance_usd=settled_balance,
            subscriptions_ytd_usd=subscriptions_ytd,
            redemptions_ytd_usd=redemptions_ytd,
            last_settlement_timestamp=_last_settlement_timestamp(subs, reds),
            movements=_build_movements(subs, reds),
        )


# ---------------------------------------------------------------------------
# Module-level default provider (swappable by tests / future HTTP client)
# ---------------------------------------------------------------------------

_provider: FundAdminProvider = InMemoryFundAdminProvider()


def get_fund_admin_provider() -> FundAdminProvider:
    """FastAPI dependency — returns the active provider.

    Tests override by calling ``set_fund_admin_provider``; Phase 5/6 will
    swap the default for an HTTP-backed client wired at app startup.
    """
    return _provider


def set_fund_admin_provider(provider: FundAdminProvider) -> None:
    """Replace the module-level provider (tests + future HTTP wiring)."""
    global _provider
    _provider = provider


__all__ = [
    "FundAdminProvider",
    "InMemoryFundAdminProvider",
    "get_fund_admin_provider",
    "set_fund_admin_provider",
]
