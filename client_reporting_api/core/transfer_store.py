"""Canonical transfer store — single source of truth for all transfer data.

All PNL, invoice, and reporting consumers read transfers through this module.
Eliminates the fragmented transfer detection across backfill_store,
pnl_chart_generator, and invoice_state.

Provides:
  - get_transfers(client_id) → list[TransferRecord]
  - get_net_deposits(client_id) → dict with native + USD totals
  - get_daily_transfers(client_id) → dict[date_str, float] for equity curve alignment
"""

from __future__ import annotations

import logging
from decimal import Decimal

from unified_api_contracts.internal import TransferRecord

from client_reporting_api.core.transfer_collector import collect_all_transfers

logger = logging.getLogger(__name__)

# In-memory cache (per process lifetime)
_cache: dict[str, list[TransferRecord]] = {}


def get_transfers(client_id: str) -> list[TransferRecord]:
    """Return all canonical transfers for a client, sorted by timestamp."""
    if client_id not in _cache:
        _cache[client_id] = collect_all_transfers(client_id)
    return _cache[client_id]


def clear_cache(client_id: str | None = None) -> None:
    """Clear cached transfers. None = clear all."""
    if client_id is None:
        _cache.clear()
    else:
        _cache.pop(client_id, None)


def get_net_deposits(client_id: str) -> dict[str, Decimal | dict[str, dict[str, Decimal]] | str]:
    """Compute net deposits per currency and total USD equivalent.

    Returns:
        {
            "net_deposits_usd": Decimal,
            "total_deposits_usd": Decimal,
            "total_withdrawals_usd": Decimal,
            "by_currency": {
                "USDT": {"deposits": Decimal, "withdrawals": Decimal, "net": Decimal},
                "BTC": {"deposits": Decimal, "withdrawals": Decimal, "net": Decimal},
            },
            "primary_currency": str,  # most-used currency
        }
    """
    transfers = get_transfers(client_id)

    by_currency: dict[str, dict[str, Decimal]] = {}
    total_deposits_usd = Decimal("0")
    total_withdrawals_usd = Decimal("0")

    for t in transfers:
        if t.status != "ok":
            continue

        cur = t.currency
        if cur not in by_currency:
            by_currency[cur] = {
                "deposits": Decimal("0"),
                "withdrawals": Decimal("0"),
                "net": Decimal("0"),
            }

        if t.direction == "deposit":
            by_currency[cur]["deposits"] += t.amount
            by_currency[cur]["net"] += t.amount
            total_deposits_usd += t.usd_amount
        elif t.direction == "withdrawal":
            by_currency[cur]["withdrawals"] += t.amount
            by_currency[cur]["net"] -= t.amount
            total_withdrawals_usd += t.usd_amount

    net_deposits_usd = total_deposits_usd - total_withdrawals_usd

    # Primary currency = the one with the most transfer volume
    primary_currency = "USDT"
    if by_currency:
        primary_currency = max(
            by_currency.keys(),
            key=lambda c: by_currency[c]["deposits"] + by_currency[c]["withdrawals"],
        )

    return {
        "net_deposits_usd": net_deposits_usd,
        "total_deposits_usd": total_deposits_usd,
        "total_withdrawals_usd": total_withdrawals_usd,
        "by_currency": by_currency,
        "primary_currency": primary_currency,
    }


def get_daily_transfers(
    client_id: str,
    currency: str | None = None,
) -> dict[str, float]:
    """Return daily net transfer amounts keyed by date string.

    Args:
        client_id: Client identifier
        currency: Filter to specific currency (None = all, converted to native)

    Returns:
        {"2026-01-15": 50000.0, "2026-02-20": -10000.0, ...}
        Positive = deposit, negative = withdrawal.
    """
    transfers = get_transfers(client_id)
    daily: dict[str, float] = {}

    for t in transfers:
        if t.status != "ok":
            continue
        if currency is not None and t.currency != currency:
            continue

        date_str = t.timestamp.strftime("%Y-%m-%d")
        amount = float(t.amount)
        if t.direction == "withdrawal":
            amount = -amount

        daily[date_str] = daily.get(date_str, 0.0) + amount

    return daily


def get_transfer_count(client_id: str) -> int:
    """Return total number of confirmed transfers for a client."""
    return sum(1 for t in get_transfers(client_id) if t.status == "ok")
