"""Trade history API routes — full order history with fills, fees, slippage.

In live mode, pulls real trades from OKX/Binance via ExchangeDataCollector.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.backfill_store import get_backfill_trades
from client_reporting_api.core.entitlement import enforce_entitlement
from client_reporting_api.core.ledger_views import read_canonical_run_fills
from client_reporting_api.core.live_data_provider import get_collector
from client_reporting_api.core.mock_performance_data import MOCK_TRADES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _decimal_to_float(val: Decimal) -> float:
    """Convert Decimal to float for JSON serialization."""
    return float(val)


def _num(value: str | float | None) -> float:
    """Coerce a trade-row numeric field (str/float/None) to float; 0.0 on garbage."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_BUY_SIDES = frozenset({"buy", "long", "supply", "yes", "back"})


def _trade_side(side: str) -> str:
    """Map a ledger fill side to the UI's ``"buy"``/``"sell"`` (the LedgerTrade enum)."""
    return "buy" if side.strip().lower() in _BUY_SIDES else "sell"


def _ledger_run_trades(client_id: str) -> tuple[str | None, list[dict[str, str | float | None]]]:
    """Project the canonical paper run's InstructionLedger fills to the trade tape.

    The PRIMARY source for a paper-trading client: resolves the SAME canonical run
    every other ledger panel (positions / pnl / instructions / reconciliation) keys
    off (so the trades tape is coherent with them — no per-endpoint run drift) and
    maps each :class:`TradeFillRecord` to the UI's ``LedgerTrade`` shape. These are
    the exact fills the reconciliation matched, so ``/trades`` shows the real run.
    Returns ``(canonical_run_id, [])`` when the client has no ledger run yet.
    """
    run_id, fills = read_canonical_run_fills(client_id)
    trades: list[dict[str, str | float | None]] = []
    for f in fills:
        qty = f.qty
        price = f.fill_price
        notional = abs(qty) * price
        trades.append(
            {
                "trade_id": f.trade_key,
                "strategy_id": f.strategy_id,
                "venue": f.venue,
                "symbol": f.instrument_key,
                "side": _trade_side(f.side),
                "quantity": _decimal_to_float(abs(qty)),
                "price": _decimal_to_float(price),
                "fee": _decimal_to_float(f.fees_in_quote),
                "fee_currency": "USDC",
                "realized_pnl": 0.0,
                "timestamp": f.tick_timestamp.isoformat(),
                "order_id": f.strategy_instruction_id,
                "trade_type": "paper",
                "notional_usd": _decimal_to_float(notional),
            }
        )
    # Newest first (the panel renders most-recent at the top).
    trades.sort(key=lambda t: str(t.get("timestamp", "")), reverse=True)
    return run_id, trades


@router.get("")
def get_trade_history(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
    symbol: str | None = Query(None, description="Filter by symbol"),
    side: str | None = Query(None, description="Filter by side (BUY or SELL)"),
    strategy_id: str | None = Query(None, description="Filter to one strategy_id (exact/substring)"),
    limit: int = Query(50, ge=1, le=500, description="Number of trades"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, object]:
    """Return paginated trade history with fills, fees, and P&L.

    Source order (fix 2026-06-20): the canonical paper-run InstructionLedger is the
    PRIMARY source — a paper-trading client's fills live in its GCS ledger, NOT in
    a live CeFi exchange feed, so the former collector→backfill chain returned an
    empty tape even though the run executed 21 fills. The ledger tape is derived
    via the SAME canonical-run resolver the positions/pnl/recon views use, so all
    panels reflect one run. When the client has no ledger run the legacy live
    collector + backfill path still serves CeFi exchange clients.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_trades(client_id, symbol, side, limit, offset)

    # PRIMARY: canonical paper-run ledger fills (coherent with the other panels).
    run_id, trades = _ledger_run_trades(client_id)
    source = "ledger"

    # Legacy CeFi exchange path: only when no paper-run ledger exists for the client.
    if run_id is None:
        collector = get_collector()
        records = collector.get_client_trades(client_id, limit=limit + offset)
        live_trades: list[dict[str, str | float | None]] = [
            {
                "trade_id": r.trade_id,
                "venue": r.venue,
                "symbol": r.symbol,
                "side": r.side.value if hasattr(r.side, "value") else str(r.side),
                "quantity": _decimal_to_float(r.quantity),
                "price": _decimal_to_float(r.price),
                "fee": _decimal_to_float(r.fee),
                "fee_currency": r.fee_currency,
                "realized_pnl": _decimal_to_float(r.realized_pnl),
                "timestamp": r.timestamp.isoformat(),
                "order_id": r.order_id,
                "trade_type": r.trade_type.value if hasattr(r.trade_type, "value") else str(r.trade_type),
                "notional_usd": _decimal_to_float(r.notional_usd if r.notional_usd else r.quantity * r.price),
            }
            for r in records
        ]
        trades = live_trades if live_trades else _backfill_trades(client_id)
        source = "live"

    if symbol:
        trades = [t for t in trades if t["symbol"] == symbol]
    if side:
        wanted = side.strip().lower()
        trades = [t for t in trades if str(t["side"]).lower() == wanted]
    if strategy_id:
        req = strategy_id.strip().lower()
        trades = [t for t in trades if req in str(t.get("strategy_id") or "").lower()]

    # Per-strategy rollup (P11.9) over the full filtered tape, BEFORE pagination so
    # the split reflects all matching trades, not just the current page.
    by_strategy: dict[str, dict[str, object]] = {}
    for t in trades:
        sid = str(t.get("strategy_id") or "") or "unattributed"
        agg = by_strategy.setdefault(sid, {"strategy_id": sid, "count": 0, "volume_usd": 0.0})
        agg["count"] = int(agg["count"]) + 1  # type: ignore[call-overload]
        agg["volume_usd"] = round(float(agg["volume_usd"]) + _num(t.get("notional_usd")), 2)  # type: ignore[arg-type]

    total = len(trades)
    trades = trades[offset : offset + limit]

    total_volume = sum(_num(t.get("notional_usd")) for t in trades)
    total_fees = sum(_num(t.get("fee")) for t in trades)
    net_pnl = sum(_num(t.get("realized_pnl")) for t in trades)

    return {
        "client_id": client_id,
        "trades": trades,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": source,
        "run_id": run_id,
        "strategy_id_filter": strategy_id,
        "by_strategy": list(by_strategy.values()),
        "aggregates": {
            "total_trades": total,
            "total_volume_usd": round(total_volume, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_realized_pnl": round(net_pnl, 2),
        },
    }


def _backfill_trades(client_id: str) -> list[dict[str, str | float | None]]:
    """Load backfilled trades and normalize to API format."""
    raw = get_backfill_trades(client_id)
    trades: list[dict[str, str | float | None]] = []
    for t in raw:
        fee_info = t.get("fee")
        fee_cost = 0.0
        fee_currency = ""
        if isinstance(fee_info, dict):
            fee_cost = float(cast(float, fee_info.get("cost", 0) or 0))
            fee_currency = str(cast(str, fee_info.get("currency", "") or ""))
        qty = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        # CCXT `cost` = amount * contractSize * price for derivatives.
        # `amount` alone is in contracts for OKX SWAP, so qty * price is wrong.
        cost_val = float(t.get("cost", 0) or 0)
        notional = cost_val if cost_val else round(qty * price, 2)
        ts_val = t.get("timestamp")
        ts_ms = int(ts_val) if ts_val is not None else 0
        ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat() if ts_ms else ""
        trades.append(
            {
                "trade_id": str(t.get("id", "")),
                "venue": str(t.get("exchange", "")),
                "symbol": str(t.get("symbol", "")),
                "side": str(t.get("side", "")).upper(),
                "quantity": qty,
                "price": price,
                "fee": fee_cost,
                "fee_currency": fee_currency,
                "realized_pnl": 0.0,
                "timestamp": ts_str,
                "order_id": str(t.get("order", "")),
                "trade_type": "MARKET",
                "notional_usd": round(notional, 2),
            }
        )
    # Sort newest first
    trades.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return trades


def _mock_trades(
    client_id: str,
    symbol: str | None,
    side: str | None,
    limit: int,
    offset: int,
) -> dict[str, object]:
    """Return mock trade data."""
    trades = list(MOCK_TRADES)
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    if side:
        trades = [t for t in trades if t.get("side") == side]

    total = len(trades)
    trades = trades[offset : offset + limit]

    total_volume = sum(float(t.get("notional_usd", 0)) for t in MOCK_TRADES)
    total_fees = sum(float(t.get("fee", 0)) for t in MOCK_TRADES)
    net_pnl = sum(float(t.get("realized_pnl", 0)) for t in MOCK_TRADES)

    return {
        "client_id": client_id,
        "trades": trades,
        "total": total,
        "offset": offset,
        "limit": limit,
        "aggregates": {
            "total_trades": len(MOCK_TRADES),
            "total_volume_usd": round(total_volume, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_realized_pnl": round(net_pnl, 2),
        },
    }
