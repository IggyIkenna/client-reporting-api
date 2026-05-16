"""Trade and order history fetch + projection for client-reporting updates.

Order records are projected to the MiFID II compliance schema (timestamp,
type, time-in-force, venue, status, slippage in basis points). Trades are
appended to ``trades.json``; orders to ``orders.json``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt

from client_reporting_api.cli.shared import DecimalEncoder

logger = logging.getLogger(__name__)


def _load_existing_trades(
    trades_path: Path,
) -> tuple[list[dict[str, str | float | None]], set[str]]:
    """Load the existing trades.json (if any) and return rows + their IDs."""
    if not trades_path.exists():
        return [], set()
    with open(trades_path) as f:
        existing: list[dict[str, str | float | None]] = json.load(f)
    return existing, {str(t.get("id", "")) for t in existing if t.get("id")}


def _resolve_trade_symbols(exchange: ccxt.Exchange) -> list[str]:
    """Build symbol list for trade fetch: open-position symbols + core pairs."""
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    try:
        positions = exchange.fetch_positions()
    except ccxt.BaseError:
        return symbols
    for pos in positions:
        sym = pos.get("symbol")
        if sym and float(pos.get("contracts", 0) or 0) != 0 and str(sym) not in symbols:
            symbols.insert(0, str(sym))
    return symbols


def _update_recent_trades(exchange: ccxt.Exchange, client_id: str, client_dir: Path, hours: int = 24) -> None:
    """Append recent trades to trades file."""
    trades_path = client_dir / "trades.json"
    existing, existing_ids = _load_existing_trades(trades_path)

    since_ms = int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
    new_count = 0
    for sym in _resolve_trade_symbols(exchange):
        try:
            trades = exchange.fetch_my_trades(symbol=sym, since=since_ms, limit=200)
        except ccxt.BaseError:
            time.sleep(0.2)
            continue
        for t in trades:
            tid = str(t.get("id", ""))
            if tid and tid not in existing_ids:
                existing.append(t)
                existing_ids.add(tid)
                new_count += 1
        time.sleep(0.2)

    if new_count > 0:
        existing.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
        with open(trades_path, "w") as f:
            json.dump(existing, f, cls=DecimalEncoder, indent=2)
        logger.info("[%s] Added %d new trades (total: %d)", client_id, new_count, len(existing))


def _load_existing_orders(
    orders_path: Path,
) -> tuple[list[dict[str, str | float | None]], set[str]]:
    """Load orders.json (if any) and return rows + their IDs."""
    if not orders_path.exists():
        return [], set()
    with open(orders_path) as f:
        existing: list[dict[str, str | float | None]] = json.load(f)
    return existing, {str(o.get("id", "")) for o in existing if o.get("id")}


def _fetch_orders_for_symbol(exchange: ccxt.Exchange, sym: str, since_ms: int) -> list[dict[str, str | float | None]]:
    """Pull orders for a single symbol, falling back to closed+open on OKX."""
    page_limit = 100 if exchange.id == "okx" else 200
    if exchange.has.get("fetchOrders"):
        return exchange.fetch_orders(symbol=sym, since=since_ms, limit=page_limit)
    raw: list[dict[str, str | float | None]] = list(
        exchange.fetch_closed_orders(symbol=sym, since=since_ms, limit=page_limit)
    )
    with contextlib.suppress(ccxt.BaseError):
        raw.extend(exchange.fetch_open_orders(symbol=sym, since=since_ms, limit=page_limit))
    return raw


def _extract_cancel_reason(info_dict: dict[str, object], venue: str) -> str | None:
    """Project venue-specific cancel/reject metadata to a single string."""
    if venue == "okx":
        src = info_dict.get("cancelSource", "")
        reason = info_dict.get("cancelSourceReason", "")
        if src or reason:
            return f"{src}: {reason}".strip(": ") or None
    elif venue == "binance":
        raw_status = str(info_dict.get("status", ""))
        if raw_status in ("CANCELED", "EXPIRED", "REJECTED", "NEW_ADL"):
            return raw_status
    return None


def _slippage_bps(
    order: dict[str, str | float | None],
    record: dict[str, str | float | None],
) -> float | None:
    """Compute the signed slippage of an order in basis points (None if N/A)."""
    if not record["price"] or not record["average"]:
        return None
    order_price = float(record["price"])
    avg_fill = float(record["average"])
    if order_price <= 0:
        return None
    slippage_bps = (avg_fill - order_price) / order_price * 10000
    if order.get("side") == "sell":
        slippage_bps = -slippage_bps
    return round(slippage_bps, 2)


def _project_order_record(order: dict[str, str | float | None], venue: str) -> dict[str, str | float | None]:
    """Build the MiFID-compliant order record from a CCXT order dict."""
    info = order.get("info") or {}
    info_dict = info if isinstance(info, dict) else {}
    record: dict[str, str | float | None] = {
        "id": str(order.get("id", "")),
        "client_order_id": order.get("clientOrderId"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "type": order.get("type"),
        "time_in_force": order.get("timeInForce"),
        "status": order.get("status"),
        "cancel_reason": _extract_cancel_reason(info_dict, venue),
        "amount": float(order.get("amount", 0) or 0),
        "price": float(order.get("price", 0) or 0) if order.get("price") else None,
        "average": float(order.get("average", 0) or 0) if order.get("average") else None,
        "filled": float(order.get("filled", 0) or 0),
        "remaining": float(order.get("remaining", 0) or 0),
        "cost": float(order.get("cost", 0) or 0),
        "fee_cost": float((order.get("fee") or {}).get("cost", 0) or 0),
        "fee_currency": (order.get("fee") or {}).get("currency"),
        "timestamp": order.get("timestamp"),
        "last_update_timestamp": info_dict.get("uTime") or info_dict.get("updateTime"),
        "last_trade_timestamp": order.get("lastTradeTimestamp"),
        "venue": venue,
        "reduce_only": order.get("reduceOnly"),
        "post_only": order.get("postOnly"),
        "stop_price": float(order.get("stopPrice", 0) or 0) if order.get("stopPrice") else None,
        "trigger_price": (float(order.get("triggerPrice", 0) or 0) if order.get("triggerPrice") else None),
    }
    slippage = _slippage_bps(order, record)
    if slippage is not None:
        record["slippage_bps"] = slippage
    return record


def _update_order_history(
    exchange: ccxt.Exchange,
    client_id: str,
    venue: str,
    client_dir: Path,
    hours: int = 24,
) -> None:
    """Fetch order history for MiFID compliance.

    MiFID II requires:
      - Order creation timestamp, type (market/limit), time-in-force
      - Execution venue, execution timestamp
      - Fill price vs order price (best execution analysis)
      - Order status (filled, cancelled, partially filled)
      - Client order ID linkage
    """
    orders_path = client_dir / "orders.json"
    existing_orders, existing_ids = _load_existing_orders(orders_path)
    since_ms = int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
    new_count = 0

    for sym in _resolve_trade_symbols(exchange):
        try:
            raw_orders = _fetch_orders_for_symbol(exchange, sym, since_ms)
        except (ccxt.BaseError, ccxt.NotSupported) as exc:
            logger.debug("[%s] Order fetch for %s: %s", client_id, sym, str(exc))
            time.sleep(0.2)
            continue
        for order in raw_orders:
            oid = str(order.get("id", ""))
            if not oid or oid in existing_ids:
                continue
            existing_orders.append(_project_order_record(order, venue))
            existing_ids.add(oid)
            new_count += 1
        time.sleep(0.2)

    if new_count > 0:
        existing_orders.sort(key=lambda o: int(o.get("timestamp", 0) or 0))
        with open(orders_path, "w") as f:
            json.dump(existing_orders, f, cls=DecimalEncoder, indent=2)
        logger.info("[%s] Added %d new orders (total: %d)", client_id, new_count, len(existing_orders))
