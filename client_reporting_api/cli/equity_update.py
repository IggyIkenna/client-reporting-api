# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportArgumentType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false
"""Equity-curve + balance/positions update helpers for incremental runs.

Given a live exchange session, these helpers compute today's equity point,
detect transfers, fill missing days, persist balance/positions snapshots,
and refresh summary metadata.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt

from client_reporting_api.cli.gcs_sync import _download_from_gcs
from client_reporting_api.cli.pnl_fetch import _fetch_pnl_since
from client_reporting_api.cli.shared import (
    DecimalEncoder,
    _create_exchange,
    _fetch_credentials,
    _GapInfo,
    _get_usd_price,
)

logger = logging.getLogger(__name__)

_BALANCE_SKIP_KEYS = {"info", "free", "used", "total", "timestamp", "datetime"}


def _load_existing_equity_curve(
    client_id: str, equity_path: Path, client_dir: Path
) -> list[dict[str, str | float]] | None:
    """Read the existing equity curve, downloading from GCS if not present locally."""
    if not equity_path.exists() and not _download_from_gcs(client_id, client_dir):
        logger.warning(
            "[%s] No data locally or in GCS — run: client-reporting-manage backfill --client %s",
            client_id,
            client_id,
        )
        return None
    with open(equity_path) as f:
        return json.load(f)


def _parse_balances(exchange: ccxt.Exchange, total_info: dict[str, object]) -> tuple[float, float, float]:
    """Extract USDT balance, BTC balance, and BTC USD price from a balance dict."""
    usdt_bal = 0.0
    btc_bal = 0.0
    btc_price = 0.0
    for cur, val in total_info.items():
        if cur in _BALANCE_SKIP_KEYS or val is None or float(val or 0) == 0:
            continue
        if cur == "USDT":
            usdt_bal = float(val or 0)
        elif cur == "BTC":
            btc_bal = float(val or 0)
            btc_price = _get_usd_price(exchange, "BTC")
    return usdt_bal, btc_bal, btc_price


def _build_equity_point(
    today: str, total_usd: float, usdt_bal: float, btc_bal: float, btc_price: float
) -> dict[str, str | float]:
    """Construct today's equity-curve point from parsed balances."""
    point: dict[str, str | float] = {"date": today, "equity_usd": round(total_usd, 2)}
    if usdt_bal > 0:
        point["usdt_balance"] = round(usdt_bal, 2)
    if btc_bal > 0:
        point["btc_balance"] = round(btc_bal, 8)
        point["btc_price_usd"] = round(btc_price, 2)
        point["btc_value_usd"] = round(btc_bal * btc_price, 2)
    return point


def _detect_transfer(
    exchange: ccxt.Exchange,
    venue: str,
    last_date: str,
    today: str,
    prev_point: dict[str, str | float],
    total_usd: float,
    btc_bal: float,
    btc_price: float,
) -> float:
    """Return a non-zero transfer amount if equity change exceeds noise threshold."""
    prev_equity = float(prev_point.get("equity_usd", 0))
    equity_change = total_usd - prev_equity
    trading_pnl = _fetch_pnl_since(exchange, venue, last_date, today)
    prev_btc = float(prev_point.get("btc_balance", 0))
    prev_btc_price = float(prev_point.get("btc_price_usd", 0))
    price_effect = prev_btc * (btc_price - prev_btc_price) if prev_btc > 0 and prev_btc_price > 0 else 0.0
    transfer = equity_change - trading_pnl - price_effect
    ref_equity = max(abs(total_usd), abs(prev_equity), 1.0)
    # USDT-only accounts (btc_bal==0): ledger query lag is noisy; require 2% or $5K.
    # Other accounts: tighter 1% / $100-$1K band.
    threshold = max(5000.0, ref_equity * 0.02) if btc_bal == 0 else max(100.0, min(ref_equity * 0.01, 1000.0))
    return round(transfer, 2) if abs(transfer) > threshold else 0.0


def _fill_gap_days(equity_curve: list[dict[str, str | float]], last_dt: datetime, today_dt: datetime) -> None:
    """Carry-forward equity for missing intermediate days (in place)."""
    d = last_dt + timedelta(days=1)
    while d < today_dt:
        gap_point: dict[str, str | float] = {
            "date": d.strftime("%Y-%m-%d"),
            "equity_usd": float(equity_curve[-1].get("equity_usd", 0)),
        }
        for key in ("usdt_balance", "btc_balance", "btc_price_usd", "btc_value_usd"):
            if key in equity_curve[-1]:
                gap_point[key] = equity_curve[-1][key]
        equity_curve.append(gap_point)
        d += timedelta(days=1)


def _persist_balance_snapshot(
    client_dir: Path, balance_raw: dict[str, object], total_info: dict[str, object]
) -> dict[str, dict[str, float]]:
    """Write the balance snapshot file and return the assets dict."""
    assets: dict[str, dict[str, float]] = {}
    for cur, val in total_info.items():
        if cur in _BALANCE_SKIP_KEYS or val is None or float(val or 0) == 0:
            continue
        assets[cur] = {
            "total": float(val or 0),
            "free": float(balance_raw.get("free", {}).get(cur, 0) or 0),
            "locked": float(balance_raw.get("used", {}).get(cur, 0) or 0),
        }
    with open(client_dir / "balance.json", "w") as f:
        json.dump(
            {"assets": assets, "timestamp": datetime.now(tz=UTC).isoformat()},
            f,
            cls=DecimalEncoder,
            indent=2,
        )
    return assets


def _persist_positions(client_dir: Path, exchange: ccxt.Exchange, client_id: str) -> None:
    """Write the open-positions snapshot, swallowing ccxt fetch errors."""
    try:
        positions = exchange.fetch_positions()
    except ccxt.BaseError as exc:
        logger.warning("[%s] Failed to fetch positions: %s", client_id, str(exc))
        return
    pos_list: list[dict[str, object]] = []
    for pos in positions:
        contracts = float(pos.get("contracts", 0) or 0)
        if contracts == 0:
            continue
        pos_list.append(
            {
                "symbol": pos.get("symbol", ""),
                "side": pos.get("side", ""),
                "contracts": contracts,
                "entryPrice": float(pos.get("entryPrice", 0) or 0),
                "markPrice": float(pos.get("markPrice", 0) or 0),
                "unrealizedPnl": float(pos.get("unrealizedPnl", 0) or 0),
                "leverage": float(pos.get("leverage", 1) or 1),
                "notional": float(pos.get("notional", 0) or 0),
                "liquidationPrice": pos.get("liquidationPrice"),
            }
        )
    with open(client_dir / "positions.json", "w") as f:
        json.dump(pos_list, f, cls=DecimalEncoder, indent=2)


def _update_summary(
    summary_path: Path,
    equity_curve: list[dict[str, str | float]],
    assets: dict[str, dict[str, float]],
) -> None:
    """Refresh the summary.json file with the latest update metadata."""
    summary: dict[str, object] = {}
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
    summary["last_update"] = datetime.now(tz=UTC).isoformat()
    summary["equity_curve_days"] = len(equity_curve)
    summary["current_equity_usd"] = assets
    with open(summary_path, "w") as f:
        json.dump(summary, f, cls=DecimalEncoder, indent=2)


def _compute_update_gap(client_id: str, equity_curve: list[dict[str, str | float]]) -> _GapInfo | None:
    """Compute the today/last-date gap. Returns None if last date is in the future."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    last_date = str(equity_curve[-1].get("date", ""))
    if last_date > today:
        logger.warning("[%s] Last date %s is in the future?", client_id, last_date)
        return None
    last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=UTC)
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    gap_days = (today_dt - last_dt).days
    if gap_days == 0:
        logger.info("[%s] Same day — refreshing snapshot", client_id)
    else:
        logger.info(
            "[%s] %d day(s) since %s — backfilling to %s",
            client_id,
            gap_days,
            last_date,
            today,
        )
    return _GapInfo(today, last_date, last_dt, today_dt, gap_days)


def _open_exchange_with_balance(client_id: str, venue: str) -> tuple[ccxt.Exchange, dict[str, object]] | None:
    """Resolve credentials, open the exchange, and fetch balance. None on failure."""
    creds = _fetch_credentials(client_id, venue)
    if creds is None:
        return None
    exchange = _create_exchange(venue, creds["api_key"], creds["api_secret"], creds["passphrase"])
    try:
        return exchange, exchange.fetch_balance()
    except ccxt.BaseError as exc:
        logger.warning("[%s] Failed to fetch balance: %s", client_id, str(exc))
        return None


def _merge_today_point_into_curve(
    equity_curve: list[dict[str, str | float]],
    point: dict[str, str | float],
    gap: _GapInfo,
) -> list[dict[str, str | float]]:
    """Insert ``point`` for today, filling any missing intermediate days."""
    if gap.gap_days > 1:
        _fill_gap_days(equity_curve, gap.last_dt, gap.today_dt)
    if gap.gap_days > 0:
        equity_curve = [p for p in equity_curve if str(p.get("date", "")) != gap.today]
        equity_curve.append(point)
    else:
        equity_curve[-1] = point
    return equity_curve


def _maybe_attach_transfer(
    point: dict[str, str | float],
    exchange: ccxt.Exchange,
    venue: str,
    prev_point: dict[str, str | float],
    gap: _GapInfo,
    total_usd: float,
    btc_bal: float,
    btc_price: float,
    client_id: str,
) -> None:
    """Detect and stamp a transfer onto today's equity point when the gap > 0."""
    if gap.gap_days <= 0:
        return
    transfer = _detect_transfer(
        exchange,
        venue,
        gap.last_date,
        gap.today,
        prev_point,
        total_usd,
        btc_bal,
        btc_price,
    )
    if transfer:
        point["transfer_usd"] = transfer
        logger.info("[%s] Detected transfer: $%.2f", client_id, transfer)
