"""Per-client attribution routes — NAV / PnL series / positions / attribution waterfall.

Routes (all under /api/v1/clients/{client_id}/):
  GET /nav         — NAV time-series (date_from / date_to query params)
  GET /pnl         — Daily PnL series
  GET /positions   — Current open positions
  GET /attribution — PnL attribution waterfall by factor × layer

SSOT: plans/active/client_reporting_pnl_attribution_mvp_2026_05_10.md Phase 4.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.attribution_reader import read_attribution_rows
from client_reporting_api.core.entitlement import _enforce_entitlement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients/{client_id}", tags=["attribution"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


# ---------------------------------------------------------------------------
# Mock helpers (CLOUD_MOCK_MODE=true / CI mode)
# ---------------------------------------------------------------------------


def _mock_nav(client_id: str, date_from: date | None, date_to: date | None) -> dict[str, object]:
    today = date.today()
    dates = [str(today)]
    return {
        "client_id": client_id,
        "share_class": "USDT",
        "mode": "DEMO",
        "snapshots": [
            {
                "date": d,
                "nav_usd": "100000.00",
                "nav_in_share_class": "100000.00",
                "nav_delta_usd": "500.00",
                "timestamp": datetime.now(UTC).isoformat(),
            }
            for d in dates
        ],
    }


def _mock_pnl(client_id: str, date_from: date | None, date_to: date | None) -> dict[str, object]:
    today = date.today()
    return {
        "client_id": client_id,
        "share_class": "USDT",
        "entries": [
            {
                "period_tag": str(today),
                "realized_pnl": "200.00",
                "unrealized_pnl": "300.00",
                "total_pnl": "500.00",
                "strategy_alpha_total": "450.00",
                "execution_alpha_total": "50.00",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }


def _mock_positions(client_id: str) -> dict[str, object]:
    return {
        "client_id": client_id,
        "positions": [
            {
                "archetype_id": "carry_staked_basis",
                "strategy_leg_id": "leg_0",
                "trade_id": None,
                "venue": "hyperliquid",
                "instrument": "BTC-PERP",
                "qty": "0.5",
                "mark_price": "62000.00",
                "cost_basis": "61500.00",
                "realized_pnl": "0.00",
                "unrealized_pnl": "250.00",
                "share_class": "USDT",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }


def _mock_attribution(
    client_id: str, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    today = date.today()
    return {
        "client_id": client_id,
        "rows": [
            {
                "strategy_id": "carry_staked_basis",
                "instrument_id": "BTC-PERP",
                "date": str(today),
                "factor": "CARRY",
                "layer": "STRATEGY",
                "amount": "300.00",
            },
            {
                "strategy_id": "carry_staked_basis",
                "instrument_id": "BTC-PERP",
                "date": str(today),
                "factor": "SLIPPAGE",
                "layer": "EXECUTION",
                "amount": "50.00",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Live helpers (reads from attribution parquet via bucket naming SSOT)
# ---------------------------------------------------------------------------


def _nav_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    """Aggregate attribution rows into NAV snapshots per date."""
    by_date: dict[str, Decimal] = {}
    for row in rows:
        ts = row.get("timestamp")
        row_date = str(ts)[:10] if ts else "unknown"
        amount_str = str(row.get("amount", "0"))
        try:
            by_date[row_date] = by_date.get(row_date, Decimal("0")) + Decimal(amount_str)
        except Exception:
            pass

    snapshots = [
        {
            "date": d,
            "nav_usd": str(total),
            "nav_in_share_class": str(total),
            "nav_delta_usd": str(total),
            "timestamp": f"{d}T00:00:00+00:00",
        }
        for d, total in sorted(by_date.items())
    ]
    return {"client_id": client_id, "share_class": "USDT", "mode": "DEMO", "snapshots": snapshots}


def _pnl_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Aggregate attribution rows into per-date PnL entries."""
    by_date: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        ts = row.get("timestamp")
        row_date = str(ts)[:10] if ts else "unknown"
        layer = str(row.get("layer", ""))
        amount_str = str(row.get("amount", "0"))
        try:
            amount = Decimal(amount_str)
        except Exception:
            continue
        if row_date not in by_date:
            by_date[row_date] = {
                "total": Decimal("0"),
                "strategy": Decimal("0"),
                "execution": Decimal("0"),
            }
        by_date[row_date]["total"] += amount
        if layer == "STRATEGY":
            by_date[row_date]["strategy"] += amount
        elif layer == "EXECUTION":
            by_date[row_date]["execution"] += amount

    entries = [
        {
            "period_tag": d,
            "realized_pnl": "0.00",
            "unrealized_pnl": str(totals["total"]),
            "total_pnl": str(totals["total"]),
            "strategy_alpha_total": str(totals["strategy"]),
            "execution_alpha_total": str(totals["execution"]),
            "timestamp": f"{d}T00:00:00+00:00",
        }
        for d, totals in sorted(by_date.items())
    ]
    return {"client_id": client_id, "share_class": "USDT", "entries": entries}


def _attribution_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Project raw attribution parquet rows to API response."""
    out = []
    for row in rows:
        ts = row.get("timestamp")
        out.append(
            {
                "strategy_id": row.get("strategy_id"),
                "instrument_id": row.get("instrument_id"),
                "date": str(ts)[:10] if ts else None,
                "factor": row.get("factor"),
                "layer": row.get("layer"),
                "amount": row.get("amount"),
                "archetype_id": row.get("archetype_id"),
                "fill_id": row.get("fill_id"),
                "venue": row.get("venue"),
                "benchmark_price": row.get("benchmark_price"),
            }
        )
    return {"client_id": client_id, "rows": out}


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/nav")
def get_client_nav(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),
) -> dict[str, object]:
    """NAV time-series for a client. Reads attribution parquet, sums amounts per date."""
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_nav(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    return _nav_from_rows(client_id, rows, date_from, date_to)


@router.get("/pnl")
def get_client_pnl(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),
) -> dict[str, object]:
    """Daily PnL series for a client. Returns strategy_alpha + execution_alpha split per day."""
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_pnl(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    return _pnl_from_rows(client_id, rows)


@router.get("/positions")
def get_client_positions(
    client_id: str,
    auth: AuthDep,
) -> dict[str, object]:
    """Current open positions for a client.

    Positions snapshot is sourced from position-balance-monitor-service parquet.
    MVP returns mock data — real feed plugged in Phase 8 demo run.
    """
    _enforce_entitlement(auth, client_id)
    return _mock_positions(client_id)


@router.get("/attribution")
def get_client_attribution(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),
) -> dict[str, object]:
    """PnL attribution waterfall by factor × layer for a client.

    Returns raw PnLAttributionRow records grouped by (strategy_id, instrument, factor, layer).
    """
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_attribution(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    return _attribution_from_rows(client_id, rows)
