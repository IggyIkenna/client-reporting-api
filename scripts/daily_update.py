# Epic: global_ledger_pnl_attribution_master
# Lifecycle: campaign:client_reporting_pnl_attribution_mvp
# Delete-when: after promoted to CLI subcommand
"""Hourly incremental update for client reporting data.

Appends new equity points and recent trades to existing backfill data.
Backfills any gaps from last known data date to today.
Much faster than a full backfill — ~30s per client vs ~3min.

Usage:
    # Update all clients
    python scripts/daily_update.py

    # Specific client
    python scripts/daily_update.py --client PR

    # Dry run
    python scripts/daily_update.py --dry-run

Schedule: run hourly via cron or Cloud Scheduler.
  crontab -e → 5 * * * * cd /path/to/client-reporting-api && python scripts/daily_update.py >> /tmp/reporting-update.log 2>&1
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import ccxt
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = WORKSPACE / "execution-service" / "configs" / "credentials-registry.yaml"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "backfill"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o: object) -> float | str:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def load_registry() -> dict[str, dict[str, str | float | bool | dict[str, float]]]:
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("clients", {})


def get_secret(name: str) -> str | None:
    from unified_trading_library import get_secret as _get_secret

    return _get_secret(name)


def create_exchange(venue: str, api_key: str, api_secret: str, passphrase: str) -> ccxt.Exchange:
    config: dict[str, str | bool | int | dict[str, str]] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": False,
        "timeout": 15000,
    }
    if venue == "okx" and passphrase:
        config["password"] = passphrase
    if venue == "binance":
        config["options"] = {"defaultType": "swap"}

    cls_map: dict[str, type[ccxt.Exchange]] = {"binance": ccxt.binance, "okx": ccxt.okx}
    cls = cls_map.get(venue)
    if cls is None:
        raise ValueError(f"Unsupported venue: {venue}")
    return cls(config)


def fetch_credentials(client_id: str, venue: str) -> dict[str, str] | None:
    base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    try:
        api_key = get_secret(f"{base}-api-key")
        api_secret = get_secret(f"{base}-api-secret")
    except RuntimeError:
        logger.warning("No credentials for %s", base)
        return None
    # UTL's get_secret returns None (not RuntimeError) on a missing secret; without
    # this guard a missing key builds a None-filled creds dict and CCXT gets apiKey=None.
    if api_key is None or api_secret is None:
        logger.warning("Missing api-key/api-secret for %s", base)
        return None
    passphrase = ""
    if venue == "okx":
        with contextlib.suppress(RuntimeError):
            passphrase = get_secret(f"{base}-passphrase") or ""
    return {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase}


def _get_usd_price(exchange: ccxt.Exchange, currency: str) -> float:
    """Get USD price for a currency."""
    stables = {"USDT", "USDC", "BUSD", "DAI", "USD", "FDUSD"}
    if currency in stables:
        return 1.0
    for quote in ["USDT", "USD", "USDC"]:
        try:
            ticker = exchange.fetch_ticker(f"{currency}/{quote}")
            price = float(ticker.get("last", 0) or ticker.get("close", 0) or 0)
            if price > 0:
                return price
        except ccxt.BaseError:
            continue
    return 0.0


def update_client(client_id: str, venue: str, currency: str, dry_run: bool = False) -> bool:
    """Incremental update for a single client — backfills any gaps then updates today."""
    client_dir = DATA_DIR / client_id
    equity_path = client_dir / "equity_curve.json"
    summary_path = client_dir / "summary.json"

    if not equity_path.exists():
        logger.warning("[%s] No existing equity curve — run full backfill first", client_id)
        return False

    # Load existing data
    with open(equity_path) as f:
        equity_curve: list[dict[str, str | float]] = json.load(f)

    if not equity_curve:
        logger.warning("[%s] Empty equity curve", client_id)
        return False

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    last_date = str(equity_curve[-1].get("date", ""))

    if last_date > today:
        logger.warning("[%s] Last date %s is in the future?", client_id, last_date)
        return False

    # Calculate how many days we need to backfill
    last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=UTC)
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    gap_days = (today_dt - last_dt).days

    if gap_days == 0:
        logger.info("[%s] Same day — updating current equity snapshot", client_id)
    else:
        logger.info(
            "[%s] Gap of %d day(s) since %s — backfilling to %s",
            client_id,
            gap_days,
            last_date,
            today,
        )

    if dry_run:
        logger.info("[%s] DRY RUN: would update equity (gap=%d days)", client_id, gap_days)
        return True

    # Fetch credentials and connect
    creds = fetch_credentials(client_id, venue)
    if creds is None:
        return False

    exchange = create_exchange(venue, creds["api_key"], creds["api_secret"], creds["passphrase"])

    # Fetch current balance
    try:
        balance_raw = exchange.fetch_balance()
    except ccxt.BaseError as exc:
        logger.warning("[%s] Failed to fetch balance: %s", client_id, str(exc))
        return False

    # Parse balances into USD
    total_info = balance_raw.get("total", {})
    skip_keys = {"info", "free", "used", "total", "timestamp", "datetime"}
    usdt_bal = 0.0
    btc_bal = 0.0
    btc_price = 0.0

    for cur, val in total_info.items():
        if cur in skip_keys or val is None or float(val) == 0:
            continue
        if cur == "USDT":
            usdt_bal = float(val)
        elif cur == "BTC":
            btc_bal = float(val)
            btc_price = _get_usd_price(exchange, "BTC")

    total_usd = usdt_bal + btc_bal * btc_price

    # Build today's point
    point: dict[str, str | float] = {
        "date": today,
        "equity_usd": round(total_usd, 2),
    }
    if usdt_bal > 0:
        point["usdt_balance"] = round(usdt_bal, 2)
    if btc_bal > 0:
        point["btc_balance"] = round(btc_bal, 8)
        point["btc_price_usd"] = round(btc_price, 2)
        point["btc_value_usd"] = round(btc_bal * btc_price, 2)

    # Transfer detection via residual method
    if gap_days > 0 and len(equity_curve) > 0:
        prev_equity = float(equity_curve[-1].get("equity_usd", 0))
        equity_change = total_usd - prev_equity

        # Fetch trading PnL since last update
        trading_pnl = _fetch_pnl_since(exchange, venue, last_date, today)

        # BTC price effect on existing position
        prev_btc = float(equity_curve[-1].get("btc_balance", 0))
        prev_btc_price = float(equity_curve[-1].get("btc_price_usd", 0))
        price_effect = prev_btc * (btc_price - prev_btc_price) if prev_btc > 0 and prev_btc_price > 0 else 0.0

        transfer = equity_change - trading_pnl - price_effect
        ref_equity = max(abs(total_usd), abs(prev_equity), 1.0)
        threshold = max(100.0, min(ref_equity * 0.01, 1000.0))
        if abs(transfer) > threshold:
            point["transfer_usd"] = round(transfer, 2)
            logger.info("[%s] Detected transfer: $%.2f", client_id, transfer)

    # Fill any gap days between last_date and today
    if gap_days > 1:
        # For multi-day gaps, carry forward last known equity for intermediate days
        gap_start = last_dt + timedelta(days=1)
        d = gap_start
        while d < today_dt:
            gap_str = d.strftime("%Y-%m-%d")
            gap_point: dict[str, str | float] = {
                "date": gap_str,
                "equity_usd": float(equity_curve[-1].get("equity_usd", 0)),
            }
            for key in ("usdt_balance", "btc_balance", "btc_price_usd", "btc_value_usd"):
                if key in equity_curve[-1]:
                    gap_point[key] = equity_curve[-1][key]
            equity_curve.append(gap_point)
            d += timedelta(days=1)

    if gap_days > 0:
        # Remove existing today entry if present (safety)
        equity_curve = [p for p in equity_curve if str(p.get("date", "")) != today]
        equity_curve.append(point)
    else:
        # Same day — update today's entry in place
        equity_curve[-1] = point

    # Save updated equity curve
    with open(equity_path, "w") as f:
        json.dump(equity_curve, f, cls=DecimalEncoder, indent=2)

    # Update balance snapshot
    balance_data: dict[str, str | dict[str, dict[str, float]]] = {
        "assets": {},
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    assets: dict[str, dict[str, float]] = {}
    for cur, val in total_info.items():
        if cur in skip_keys or val is None or float(val) == 0:
            continue
        free_val = float(balance_raw.get("free", {}).get(cur, 0) or 0)
        locked_val = float(balance_raw.get("used", {}).get(cur, 0) or 0)
        assets[cur] = {
            "total": float(val),
            "free": free_val,
            "locked": locked_val,
        }
    balance_data["assets"] = assets
    with open(client_dir / "balance.json", "w") as f:
        json.dump(balance_data, f, cls=DecimalEncoder, indent=2)

    # Fetch recent trades (last 24h)
    _update_recent_trades(exchange, client_id, venue, client_dir)

    # Update positions
    try:
        positions = exchange.fetch_positions()
        pos_list = []
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
    except ccxt.BaseError as exc:
        logger.warning("[%s] Failed to fetch positions: %s", client_id, str(exc))

    # Update summary
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
    else:
        summary = {}
    summary["last_update"] = datetime.now(tz=UTC).isoformat()
    summary["equity_curve_days"] = len(equity_curve)
    summary["current_equity_usd"] = assets
    with open(summary_path, "w") as f:
        json.dump(summary, f, cls=DecimalEncoder, indent=2)

    logger.info(
        "[%s] Updated: equity=$%.2f, %d days, gap=%d",
        client_id,
        total_usd,
        len(equity_curve),
        gap_days,
    )
    return True


def _fetch_pnl_since(exchange: ccxt.Exchange, venue: str, since_date: str, until_date: str) -> float:
    """Fetch trading PnL from since_date to until_date via bills/income ledger."""
    since_ms = int(datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime.strptime(until_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms += 86400 * 1000  # Include full day
    total_pnl = 0.0

    if venue == "okx":
        try:
            # OKX bills API paginates by time, fetch in chunks
            cursor_ms = since_ms
            while cursor_ms < until_ms:
                end_ms = min(cursor_ms + 7 * 86400 * 1000, until_ms)  # 7-day windows
                resp = exchange.private_get_account_bills(
                    {
                        "begin": str(cursor_ms),
                        "end": str(end_ms),
                        "limit": "100",
                    }
                )
                bills = resp.get("data", [])
                for bill in bills:
                    sub = str(bill.get("subType", ""))
                    if sub in ("11", "12"):
                        continue  # Skip transfer bills
                    ccy = str(bill.get("ccy", ""))
                    change = float(bill.get("balChg", 0) or 0)
                    if ccy == "USDT":
                        total_pnl += change
                    elif ccy == "BTC":
                        total_pnl += change * _get_usd_price(exchange, "BTC")
                if len(bills) < 100:
                    cursor_ms = end_ms
                else:
                    # More pages — advance cursor past last bill timestamp
                    last_ts = max(int(b.get("ts", 0) or 0) for b in bills)
                    cursor_ms = last_ts + 1 if last_ts > cursor_ms else end_ms
                time.sleep(0.2)
        except ccxt.BaseError as exc:
            logger.warning("Failed to fetch bills: %s", str(exc))

    elif venue == "binance":
        try:
            cursor_ms = since_ms
            while cursor_ms < until_ms:
                income = exchange.fapiprivate_get_income(
                    {
                        "startTime": str(cursor_ms),
                        "endTime": str(min(cursor_ms + 7 * 86400 * 1000, until_ms)),
                        "limit": "1000",
                    }
                )
                for r in income:
                    if str(r.get("incomeType", "")) == "TRANSFER":
                        continue
                    total_pnl += float(r.get("income", 0) or 0)
                if len(income) < 1000:
                    cursor_ms += 7 * 86400 * 1000
                else:
                    last_ts = max(int(r.get("time", 0) or 0) for r in income)
                    cursor_ms = last_ts + 1
                time.sleep(0.2)
        except ccxt.BaseError as exc:
            logger.warning("Failed to fetch income: %s", str(exc))

    return total_pnl


def _update_recent_trades(
    exchange: ccxt.Exchange,
    client_id: str,
    venue: str,
    client_dir: Path,
) -> None:
    """Append recent trades (last 24h) to the trades file."""
    trades_path = client_dir / "trades.json"
    existing_trades: list[dict[str, str | float | None]] = []
    existing_ids: set[str] = set()

    if trades_path.exists():
        with open(trades_path) as f:
            existing_trades = json.load(f)
        existing_ids = {str(t.get("id", "")) for t in existing_trades if t.get("id")}

    since_ms = int((datetime.now(tz=UTC) - timedelta(hours=24)).timestamp() * 1000)
    new_count = 0

    # Fetch from core symbols + open positions
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    try:
        positions = exchange.fetch_positions()
        for pos in positions:
            sym = pos.get("symbol")
            if sym and float(pos.get("contracts", 0) or 0) != 0 and str(sym) not in symbols:
                symbols.insert(0, str(sym))
    except ccxt.BaseError:
        pass

    for sym in symbols:
        try:
            trades = exchange.fetch_my_trades(symbol=sym, since=since_ms, limit=200)
            for t in trades:
                tid = str(t.get("id", ""))
                if tid and tid not in existing_ids:
                    existing_trades.append(t)
                    existing_ids.add(tid)
                    new_count += 1
        except ccxt.BaseError:
            pass
        time.sleep(0.2)

    if new_count > 0:
        existing_trades.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
        with open(trades_path, "w") as f:
            json.dump(existing_trades, f, cls=DecimalEncoder, indent=2)
        logger.info("[%s] Added %d new trades (total: %d)", client_id, new_count, len(existing_trades))


def main() -> None:
    parser = argparse.ArgumentParser(description="Hourly incremental update for client reporting")
    parser.add_argument("--client", type=str, help="Specific client ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    registry = load_registry()
    clients: list[tuple[str, dict[str, str | float | bool | dict[str, float]]]] = []

    for cid, cfg in registry.items():
        if not cfg.get("is_active", False) or cfg.get("tranche") != "managed" or not cfg.get("secret_name"):
            continue
        if args.client and cid != args.client:
            continue
        clients.append((cid, cfg))

    if not clients:
        logger.error("No clients to update")
        sys.exit(1)

    logger.info("Incremental update for %d client(s)", len(clients))
    results: dict[str, bool] = {}

    for cid, cfg in clients:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", "USDT"))
        success = update_client(cid, venue, currency, dry_run=args.dry_run)
        results[cid] = success
        time.sleep(0.5)

    logger.info("\n=== UPDATE SUMMARY ===")
    ok = sum(1 for v in results.values() if v)
    fail = sum(1 for v in results.values() if not v)
    for cid, success in results.items():
        logger.info("  %s: %s", cid, "OK" if success else "FAILED")
    logger.info("  %d/%d succeeded", ok, ok + fail)


if __name__ == "__main__":
    main()
