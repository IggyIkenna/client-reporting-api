# Epic: global_ledger_pnl_attribution_master
# Lifecycle: campaign:client_reporting_pnl_attribution_mvp
# Delete-when: after promoted to CLI subcommand
"""Backfill historical trades, deposits, withdrawals, and income ledger.

Pulls full history from OKX and Binance via CCXT, stores as JSON in
data/backfill/{client_id}/ for each client.

Equity curve reconstruction uses exchange INCOME/BILLS ledger (not just trade
fills), capturing realized PnL, funding fees, commissions, and transfers.
This matches 3rd-party platforms like TradeLink.

  - Binance: /fapi/v1/income — full history (realized PnL, funding, commissions)
  - OKX: /api/v5/account/bills + /bills-archive — running balance per event

Usage:
    # All clients
    python scripts/backfill_history.py

    # Specific client
    python scripts/backfill_history.py --client PR

    # Dry run (just show what would be fetched)
    python scripts/backfill_history.py --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import logging
import sys
import time
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import ccxt
import yaml

# --- Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = WORKSPACE / "execution-service" / "configs" / "credentials-registry.yaml"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "backfill"

# Binance core pairs to scan for trade history
BINANCE_CORE_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "DOT/USDT:USDT",
    "MATIC/USDT:USDT",
    "BNB/USDT:USDT",
    "LTC/USDT:USDT",
    "UNI/USDT:USDT",
    "ATOM/USDT:USDT",
    "FIL/USDT:USDT",
    "APT/USDT:USDT",
    "ARB/USDT:USDT",
    "OP/USDT:USDT",
    "SUI/USDT:USDT",
    "NEAR/USDT:USDT",
    "PEPE/USDT:USDT",
    "WIF/USDT:USDT",
    "FET/USDT:USDT",
    "RENDER/USDT:USDT",
    "TIA/USDT:USDT",
    "INJ/USDT:USDT",
    "SEI/USDT:USDT",
    "AAVE/USDT:USDT",
    "MKR/USDT:USDT",
    "CRV/USDT:USDT",
]

# BTC-denominated pairs for BTC accounts
BTC_PAIRS = [
    "ETH/BTC",
    "SOL/BTC",
    "XRP/BTC",
    "BNB/BTC",
]


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal."""

    def default(self, o: object) -> float | str:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def load_registry() -> dict[str, dict[str, str | float | bool | dict[str, float]]]:
    """Load credentials registry."""
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("clients", {})


def get_secret(name: str) -> str:
    """Fetch a secret from GCP Secret Manager via the workspace library."""
    from unified_trading_library import get_secret as _get_secret

    return _get_secret(name)


def create_exchange(
    venue: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> ccxt.Exchange:
    """Create a CCXT exchange instance."""
    config: dict[str, str | bool | int | dict[str, str]] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": False,  # We manage rate limiting manually
        "timeout": 15000,  # 15s socket timeout to avoid hung connections
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
    """Fetch API credentials from Secret Manager."""
    base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    try:
        api_key = get_secret(f"{base}-api-key")
        api_secret = get_secret(f"{base}-api-secret")
    except RuntimeError:
        logger.warning("No credentials for %s", base)
        return None

    passphrase = ""
    if venue == "okx":
        with contextlib.suppress(RuntimeError):
            passphrase = get_secret(f"{base}-passphrase")

    return {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase}


def fetch_all_trades_binance(
    exchange: ccxt.Exchange,
    currency: str,
) -> list[dict[str, str | float | None]]:
    """Fetch all trades from Binance since inception.

    Binance requires a symbol and supports unlimited history.
    Strategy: scan 7-day windows going forward from Jan 2025.
    """
    pairs = list(BTC_PAIRS if currency == "BTC" else BINANCE_CORE_PAIRS)
    all_trades: list[dict[str, str | float | None]] = []
    seen_ids: set[str] = set()

    # Discover symbols from open positions
    try:
        positions = exchange.fetch_positions()
        for pos in positions:
            sym = pos.get("symbol")
            contracts = float(pos.get("contracts", 0) or 0)
            if sym and contracts != 0 and str(sym) not in pairs:
                pairs = [str(sym), *pairs]
    except ccxt.BaseError:
        pass

    for symbol in pairs:
        logger.info("  Binance trades: %s", symbol)
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        month_ms = 30 * 86_400_000
        # Scan monthly from 18 months ago — trades could start at any point
        cursor = now_ms - (18 * month_ms)
        empty_months = 0
        sym_count = 0

        while cursor < now_ms and empty_months < 18:
            try:
                trades = exchange.fetch_my_trades(
                    symbol=symbol,
                    since=cursor,
                    limit=1000,
                )
            except ccxt.BaseError as exc:
                logger.warning("    Binance error %s: %s", symbol, str(exc))
                break

            new_count = 0
            for t in trades:
                tid = str(t.get("id", ""))
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_trades.append(t)
                    new_count += 1
                    sym_count += 1

            if not trades:
                empty_months += 1
                cursor += month_ms
            else:
                empty_months = 0
                last_ts = max(int(t.get("timestamp", 0) or 0) for t in trades)
                cursor = last_ts + 1

            time.sleep(0.1)

        if sym_count > 0:
            logger.info("    %s: %d trades", symbol, sym_count)

    all_trades.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
    logger.info("  Binance total: %d trades", len(all_trades))
    return all_trades


def _get_okx_traded_symbols(exchange: ccxt.Exchange) -> list[str]:
    """Discover symbols with trades on OKX from positions + a quick scan."""
    symbols: set[str] = set()

    # Get symbols from open positions
    try:
        positions = exchange.fetch_positions()
        for pos in positions:
            sym = pos.get("symbol")
            contracts = float(pos.get("contracts", 0) or 0)
            if sym and contracts != 0:
                symbols.add(str(sym))
    except ccxt.BaseError:
        pass

    # Also do a quick instType=SWAP scan to find symbols with recent trades
    try:
        recent = exchange.fetch_my_trades(
            None,
            limit=100,
            params={"instType": "SWAP"},
        )
        for t in recent:
            sym = t.get("symbol")
            if sym:
                symbols.add(str(sym))
    except ccxt.BaseError:
        pass

    # Always include core pairs
    for pair in (
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "XRP/USDT:USDT",
        "DOGE/USDT:USDT",
        "ADA/USDT:USDT",
        "AVAX/USDT:USDT",
        "LINK/USDT:USDT",
        "DOT/USDT:USDT",
        "MATIC/USDT:USDT",
        "BNB/USDT:USDT",
        "LTC/USDT:USDT",
        "UNI/USDT:USDT",
        "ATOM/USDT:USDT",
        "FIL/USDT:USDT",
        "APT/USDT:USDT",
        "ARB/USDT:USDT",
        "OP/USDT:USDT",
        "SUI/USDT:USDT",
        "NEAR/USDT:USDT",
        "PEPE/USDT:USDT",
    ):
        symbols.add(pair)

    return list(symbols)


def _fetch_okx_archive_trades(
    exchange: ccxt.Exchange,
    inst_id: str,
    contract_size: float = 1.0,
) -> list[dict[str, str | float | None]]:
    """Fetch archived trades from OKX fills-archive endpoint (older than 90 days).

    OKX /api/v5/trade/fills-archive returns fills older than 3 months.
    Requires instType + instId. Paginate via billId.
    """
    trades: list[dict[str, str | float | None]] = []
    after: str | None = None
    page = 0

    while True:
        params: dict[str, str] = {
            "instType": "SWAP",
            "instId": inst_id,
        }
        if after:
            params["after"] = after

        try:
            resp = exchange.private_get_trade_fills_archive(params)
            data = resp.get("data", [])
        except Exception as exc:
            logger.warning("    Archive error %s: %s", inst_id, str(exc))
            break

        if not data:
            break

        for fill in data:
            ts_ms = int(fill.get("ts", 0))
            fill_sz = float(fill.get("fillSz", 0) or 0)
            fill_px = float(fill.get("fillPx", 0) or 0)
            trades.append(
                {
                    "id": fill.get("tradeId", ""),
                    "symbol": inst_id,
                    "side": fill.get("side", ""),
                    "amount": fill_sz,
                    "price": fill_px,
                    "cost": fill_sz * contract_size * fill_px,
                    "timestamp": ts_ms,
                    "fee": {
                        "cost": float(fill.get("fee", 0) or 0),
                        "currency": fill.get("feeCcy", ""),
                    },
                    "order": fill.get("ordId", ""),
                    "info": fill,
                }
            )

        # Paginate using billId of last record
        after = str(data[-1].get("billId", ""))
        page += 1

        if page % 10 == 0:
            logger.info("    Archive %s page %d, %d fills", inst_id, page, len(trades))

        time.sleep(0.2)

    return trades


# OKX instId format: "BTC-USDT-SWAP" for "BTC/USDT:USDT"
def _ccxt_symbol_to_okx_inst_id(symbol: str) -> str:
    """Convert CCXT symbol to OKX instId."""
    # BTC/USDT:USDT -> BTC-USDT-SWAP
    base = symbol.split("/")[0]
    quote = symbol.split("/")[1].split(":")[0]
    return f"{base}-{quote}-SWAP"


def fetch_all_trades_okx(
    exchange: ccxt.Exchange,
) -> list[dict[str, str | float | None]]:
    """Fetch all trades from OKX since inception.

    Phase 1: Standard API (fetch_my_trades) — last ~90 days per symbol.
    Phase 2: Archive API (/trade/fills-archive) — older than 90 days.
    """
    symbols = _get_okx_traded_symbols(exchange)
    all_trades: list[dict[str, str | float | None]] = []
    seen_ids: set[str] = set()
    symbols_with_trades: list[str] = []

    # Phase 1: Standard API (recent 90 days)
    logger.info("  Phase 1: Standard API (last 90 days)")
    for symbol in symbols:
        end_time: int | None = None
        page = 0
        sym_count = 0

        while True:
            params: dict[str, int | str] = {}
            if end_time is not None:
                params["end"] = end_time

            try:
                trades = exchange.fetch_my_trades(
                    symbol=symbol,
                    limit=100,
                    params=params,
                )
            except ccxt.BaseError as exc:
                logger.warning("    OKX error %s: %s", symbol, str(exc))
                break

            if not trades:
                break

            new_count = 0
            for t in trades:
                tid = str(t.get("id", ""))
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_trades.append(t)
                    new_count += 1
                    sym_count += 1

            if new_count == 0:
                break

            oldest_ts = min(int(t.get("timestamp", 0) or 0) for t in trades)
            end_time = oldest_ts - 1
            page += 1

            if page % 10 == 0:
                logger.info("    %s page %d, %d trades", symbol, page, sym_count)

            time.sleep(0.15)

        if sym_count > 0:
            logger.info("    %s: %d trades (recent)", symbol, sym_count)
            symbols_with_trades.append(symbol)

    recent_count = len(all_trades)
    logger.info("  Phase 1 done: %d trades from %d symbols", recent_count, len(symbols_with_trades))

    # Phase 2: Archive API (older than 90 days)
    logger.info("  Phase 2: Archive API (older fills)")
    for symbol in symbols_with_trades:
        inst_id = _ccxt_symbol_to_okx_inst_id(symbol)
        # Get contract size from CCXT markets (e.g. BTC=0.01, ETH=0.1, SOL=1.0)
        mkt = exchange.markets.get(symbol, {})
        ct_size = float(mkt.get("contractSize", 1) or 1)
        archive = _fetch_okx_archive_trades(exchange, inst_id, contract_size=ct_size)
        new_count = 0
        for t in archive:
            tid = str(t.get("id", ""))
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                all_trades.append(t)
                new_count += 1
        if new_count > 0:
            logger.info("    %s: %d archive trades", symbol, new_count)

    all_trades.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
    logger.info("  OKX total: %d trades across %d symbols", len(all_trades), len(symbols))
    return all_trades


def fetch_all_transfers(
    exchange: ccxt.Exchange,
) -> dict[str, list[dict[str, str | float | None]]]:
    """Fetch all deposits and withdrawals."""
    result: dict[str, list[dict[str, str | float | None]]] = {
        "deposits": [],
        "withdrawals": [],
    }

    # Deposits — paginate by since
    logger.info("  Fetching deposits...")
    since_ms: int | None = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
    while True:
        try:
            deps = exchange.fetch_deposits(code=None, since=since_ms, limit=100)
        except ccxt.BaseError as exc:
            logger.warning("  Deposit error: %s", str(exc))
            break
        if not deps:
            break
        result["deposits"].extend(deps)
        last_ts = max(int(d.get("timestamp", 0) or 0) for d in deps)
        if last_ts <= (since_ms or 0):
            break
        since_ms = last_ts + 1
        time.sleep(0.15)

    # Withdrawals — paginate by since
    logger.info("  Fetching withdrawals...")
    since_ms = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
    while True:
        try:
            wds = exchange.fetch_withdrawals(code=None, since=since_ms, limit=100)
        except ccxt.BaseError as exc:
            logger.warning("  Withdrawal error: %s", str(exc))
            break
        if not wds:
            break
        result["withdrawals"].extend(wds)
        last_ts = max(int(w.get("timestamp", 0) or 0) for w in wds)
        if last_ts <= (since_ms or 0):
            break
        since_ms = last_ts + 1
        time.sleep(0.15)

    logger.info(
        "  Transfers: %d deposits, %d withdrawals",
        len(result["deposits"]),
        len(result["withdrawals"]),
    )
    return result


def fetch_balance_snapshot(
    exchange: ccxt.Exchange,
) -> dict[str, float | dict[str, float]]:
    """Fetch current balance snapshot."""
    try:
        bal = exchange.fetch_balance()
        total = bal.get("total", {})
        free = bal.get("free", {})
        used = bal.get("used", {})
        skip = {"info", "free", "used", "total", "timestamp", "datetime"}

        assets: dict[str, dict[str, float]] = {}
        for cur, val in total.items():
            if cur in skip or val is None or float(val) == 0:
                continue
            assets[cur] = {
                "total": float(val),
                "free": float(free.get(cur, 0) or 0),
                "locked": float(used.get(cur, 0) or 0),
            }

        return {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "assets": assets,
        }
    except ccxt.BaseError as exc:
        logger.warning("  Balance error: %s", str(exc))
        return {"timestamp": datetime.now(tz=UTC).isoformat(), "assets": {}}


def fetch_positions_snapshot(
    exchange: ccxt.Exchange,
) -> list[dict[str, str | float]]:
    """Fetch current positions."""
    positions: list[dict[str, str | float]] = []
    try:
        raw = exchange.fetch_positions()
        for pos in raw:
            contracts = float(pos.get("contracts", 0) or 0)
            if contracts == 0:
                continue
            positions.append(
                {
                    "symbol": str(pos.get("symbol", "")),
                    "side": str(pos.get("side", "long")),
                    "contracts": contracts,
                    "entry_price": float(pos.get("entryPrice", 0) or 0),
                    "mark_price": float(pos.get("markPrice", 0) or 0),
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0),
                    "leverage": float(pos.get("leverage", 1) or 1),
                    "liquidation_price": float(pos.get("liquidationPrice", 0) or 0),
                    "notional": float(pos.get("notional", 0) or 0),
                }
            )
    except ccxt.BaseError as exc:
        logger.warning("  Positions error: %s", str(exc))
    return positions


def fetch_binance_income_ledger(
    exchange: ccxt.Exchange,
) -> list[dict[str, str | float]]:
    """Fetch complete income history from Binance futures /fapi/v1/income.

    Returns every account event: REALIZED_PNL, FUNDING_FEE, COMMISSION, TRANSFER.
    Goes back to account inception — unlimited history.
    """
    all_income: list[dict[str, str | float]] = []
    end_time: int | None = None
    page = 0

    while True:
        params: dict[str, str | int] = {"limit": 1000}
        if end_time:
            params["endTime"] = end_time

        try:
            resp = exchange.fapiprivate_get_income(params)
        except ccxt.BaseError as exc:
            logger.warning("Binance income error at page %d: %s", page, str(exc))
            break

        if not resp:
            break

        for r in resp:
            all_income.append(
                {
                    "timestamp": int(r.get("time", 0)),
                    "type": str(r.get("incomeType", "")),
                    "amount": float(r.get("income", 0) or 0),
                    "asset": str(r.get("asset", "")),
                    "symbol": str(r.get("symbol", "")),
                }
            )

        oldest_ts = min(int(r.get("time", 0)) for r in resp)
        end_time = oldest_ts - 1
        page += 1

        if page % 10 == 0:
            dt = datetime.fromtimestamp(oldest_ts / 1000, tz=UTC)
            logger.info("  Income page %d, %d records, oldest: %s", page, len(all_income), dt)

        time.sleep(0.2)
        if page > 500:
            break

    all_income.sort(key=lambda r: r["timestamp"])
    logger.info("  Binance income ledger: %d records", len(all_income))
    return all_income


def fetch_okx_bills_ledger(
    exchange: ccxt.Exchange,
) -> list[dict[str, str | float]]:
    """Fetch account bills from OKX (recent + archive).

    Each bill has a running balance (`bal`) and change (`balChg`).
    bills = 7 days, bills-archive = up to 3 months.
    """
    all_bills: list[dict[str, str | float]] = []
    seen_ids: set[str] = set()

    for endpoint_name, endpoint_fn in [
        ("bills", exchange.private_get_account_bills),
        ("bills-archive", exchange.private_get_account_bills_archive),
    ]:
        after: str | None = None
        page = 0
        consecutive_errors = 0

        while True:
            params: dict[str, str] = {"limit": "100"}
            if after:
                params["after"] = after

            try:
                resp = endpoint_fn(params)
                data = resp.get("data", [])
                consecutive_errors = 0  # Reset on success
            except (ccxt.RequestTimeout, ccxt.NetworkError) as exc:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logger.warning(
                        "  %d consecutive timeouts on %s, moving on",
                        consecutive_errors,
                        endpoint_name,
                    )
                    break
                logger.info(
                    "  Timeout on %s page %d, retrying in 5s: %s",
                    endpoint_name,
                    page,
                    str(exc)[:80],
                )
                time.sleep(5)
                continue
            except ccxt.BaseError as exc:
                if "50011" in str(exc):
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        logger.warning("  Too many rate limits on %s, moving on", endpoint_name)
                        break
                    logger.info("  Rate limited on %s page %d, waiting 2s...", endpoint_name, page)
                    time.sleep(2)
                    continue
                logger.warning("  OKX %s error at page %d: %s", endpoint_name, page, str(exc))
                break

            if not data:
                break

            for b in data:
                bill_id = str(b.get("billId", ""))
                if bill_id in seen_ids:
                    continue
                seen_ids.add(bill_id)

                all_bills.append(
                    {
                        "timestamp": int(b.get("ts", 0)),
                        "bill_id": bill_id,
                        "type": str(b.get("type", "")),
                        "sub_type": str(b.get("subType", "")),
                        "balance": float(b.get("bal", 0) or 0),
                        "change": float(b.get("balChg", 0) or 0),
                        "inst_id": str(b.get("instId", "")),
                        "currency": str(b.get("ccy", "")),
                    }
                )

            after = str(data[-1].get("billId", ""))
            page += 1

            if page % 20 == 0:
                ts = int(data[-1].get("ts", 0))
                dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
                logger.info("  %s page %d, %d bills, oldest: %s", endpoint_name, page, len(all_bills), dt)

            time.sleep(0.3)  # Slower to avoid rate limits
            if page > 1000:
                break

    # Phase 3: History archive (older than 3 months, quarterly CSV downloads)
    # These need to be requested first (POST), then downloaded (GET).
    # If already requested, try to download.
    now = datetime.now(tz=UTC)
    quarters: list[tuple[str, str]] = []
    for year in range(now.year - 1, now.year + 1):
        for q in ["Q1", "Q2", "Q3", "Q4"]:
            quarters.append((str(year), q))

    for year, quarter in quarters:
        # Request generation (idempotent)
        with contextlib.suppress(ccxt.BaseError):
            exchange.private_post_account_bills_history_archive(
                {"year": year, "quarter": quarter},
            )
        time.sleep(0.5)

        # Try to download
        try:
            resp = exchange.private_get_account_bills_history_archive(
                {"year": year, "quarter": quarter},
            )
            data_list = resp.get("data", [])
            if not data_list:
                continue
            entry = data_list[0]
            state = entry.get("state", "")
            href = entry.get("fileHref", "")
            if state != "finished" or not href:
                if state == "ongoing":
                    logger.info("  Archive %s %s: still generating", year, quarter)
                continue

            # Download and parse CSV
            http_resp = urllib.request.urlopen(href, timeout=30)
            content = http_resp.read()
            archive_count = 0

            if content[:2] == b"PK":  # ZIP file
                z = zipfile.ZipFile(io.BytesIO(content))
                for name in z.namelist():
                    if not name.endswith(".csv"):
                        continue
                    with z.open(name) as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, "utf-8"))
                        for row in reader:
                            ts = int(row.get("ts", 0) or 0)
                            bill_id = str(row.get("billId", ""))
                            if bill_id in seen_ids:
                                continue
                            seen_ids.add(bill_id)
                            all_bills.append(
                                {
                                    "timestamp": ts,
                                    "bill_id": bill_id,
                                    "type": str(row.get("instType", "")),
                                    "sub_type": str(row.get("subType", "")),
                                    "balance": float(row.get("bal", 0) or 0),
                                    "change": float(row.get("balChg", 0) or 0),
                                    "inst_id": str(row.get("instId", "")),
                                    "currency": str(row.get("ccy", "")),
                                }
                            )
                            archive_count += 1

            if archive_count > 0:
                logger.info("  Archive %s %s: %d bills", year, quarter, archive_count)

        except ccxt.BaseError:
            pass
        except Exception as exc:
            logger.warning("  Archive %s %s download error: %s", year, quarter, str(exc)[:100])
        time.sleep(0.5)

    all_bills.sort(key=lambda b: b["timestamp"])
    logger.info("  OKX bills ledger: %d records", len(all_bills))
    return all_bills


def compute_daily_equity_from_binance_income(
    income: list[dict[str, str | float]],
    current_balance: float,
) -> list[dict[str, str | float]]:
    """Reconstruct daily equity from Binance income ledger.

    Approach: aggregate daily net income (PnL + funding - commissions + transfers),
    then replay forward from first event. Anchor final day to current balance to
    correct for any drift.
    """
    if not income:
        return []

    # Group by day: sum all income events, and track transfers separately
    daily_net: dict[str, float] = {}
    daily_transfers: dict[str, float] = {}
    for r in income:
        ts = int(r.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        day = dt.strftime("%Y-%m-%d")
        amount = float(r.get("amount", 0) or 0)
        daily_net[day] = daily_net.get(day, 0.0) + amount
        if str(r.get("type", "")) == "TRANSFER":
            daily_transfers[day] = daily_transfers.get(day, 0.0) + amount

    sorted_days = sorted(daily_net.keys())
    if not sorted_days:
        return []

    # Replay forward: balance starts at 0, each day adds net income
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    start_date = datetime.strptime(sorted_days[0], "%Y-%m-%d").replace(tzinfo=UTC)
    end_date = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)

    equity_curve: list[dict[str, str | float]] = []
    running_balance = 0.0
    d = start_date
    while d <= end_date:
        day_str = d.strftime("%Y-%m-%d")
        running_balance += daily_net.get(day_str, 0.0)
        point: dict[str, str | float] = {
            "date": day_str,
            "equity_usd": round(running_balance, 2),
        }
        # Embed transfer for TWR calculation
        transfer = daily_transfers.get(day_str, 0.0)
        if abs(transfer) > 0.01:
            point["transfer_usd"] = round(transfer, 2)
        equity_curve.append(point)
        d += timedelta(days=1)

    # Drift correction: if computed equity != live balance, the gap is
    # undetected deposits/withdrawals (e.g. spot→futures transfers not in income ledger).
    # Add the gap as a transfer on the first day so TWR handles it correctly.
    if equity_curve and current_balance != 0:
        final_computed = float(equity_curve[-1]["equity_usd"])
        drift = current_balance - final_computed
        if abs(drift) > 1.0:
            logger.info(
                "  Drift correction: computed $%.2f vs live $%.2f (gap $%.2f = undetected transfers)",
                final_computed,
                current_balance,
                drift,
            )
            # Distribute gap as transfer on first day (initial deposit not in income ledger)
            existing_transfer = float(equity_curve[0].get("transfer_usd", 0))
            equity_curve[0]["transfer_usd"] = round(existing_transfer + drift, 2)
            # Also shift the entire equity curve up by the gap
            for point in equity_curve:
                point["equity_usd"] = round(float(point["equity_usd"]) + drift, 2)

    return equity_curve


def _fetch_btc_daily_prices(start_date: str, end_date: str) -> dict[str, float]:
    """Fetch daily BTC/USD close prices from OKX public API (no auth needed).

    Returns dict: date string -> USD price.
    """
    prices: dict[str, float] = {}
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000) + 86400000

    # OKX public candle API: max 100 per request, paginate backwards from end
    base_url = "https://www.okx.com/api/v5/market/history-candles"
    after = str(end_ts)

    while True:
        url = f"{base_url}?instId=BTC-USDT&bar=1D&limit=100&after={after}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            candles = data.get("data", [])
        except Exception as exc:
            logger.warning("  BTC price fetch error: %s", str(exc)[:80])
            break

        if not candles:
            break

        for c in candles:
            ts = int(c[0])
            close_price = float(c[4])  # [ts, o, h, l, c, vol, ...]
            dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
            day = dt.strftime("%Y-%m-%d")
            prices[day] = close_price

        oldest_ts = int(candles[-1][0])
        if oldest_ts <= start_ts:
            break
        after = str(oldest_ts)
        time.sleep(0.2)

    logger.info("  Fetched %d daily BTC prices (%s to %s)", len(prices), start_date, end_date)
    return prices


def compute_daily_equity_from_okx_bills(
    bills: list[dict[str, str | float]],
    transfers: dict[str, list[dict[str, str | float | None]]],
    current_balance_assets: dict[str, dict[str, float]],
) -> list[dict[str, str | float]]:
    """Reconstruct daily equity from OKX bills ledger.

    OKX bills include `bal` (running balance after event). Take end-of-day balance
    from the last bill each day. Tracks per-currency balances (USDT + BTC) and
    converts to USD using historical BTC prices.
    """
    if not bills:
        return []

    # Extract end-of-day balance per currency from bills (last bill of each day per ccy)
    # Key: (day, currency) -> balance
    daily_balance_per_ccy: dict[str, dict[str, float]] = {}
    daily_last_ts_per_ccy: dict[str, dict[str, int]] = {}

    for b in bills:
        ts = int(b.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        ccy = str(b.get("currency", "USDT"))
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        day = dt.strftime("%Y-%m-%d")

        if day not in daily_balance_per_ccy:
            daily_balance_per_ccy[day] = {}
            daily_last_ts_per_ccy[day] = {}

        # Keep the LATEST bill of each day per currency
        prev_ts = daily_last_ts_per_ccy[day].get(ccy, 0)
        if ts > prev_ts:
            daily_last_ts_per_ccy[day][ccy] = ts
            daily_balance_per_ccy[day][ccy] = float(b.get("balance", 0) or 0)

    # Identify which currencies are in play
    all_currencies: set[str] = set()
    for day_ccys in daily_balance_per_ccy.values():
        all_currencies.update(day_ccys.keys())

    has_btc = "BTC" in all_currencies
    need_btc_prices = has_btc

    # Determine date range
    all_days = sorted(daily_balance_per_ccy.keys())
    if not all_days:
        return []

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    start_date = datetime.strptime(all_days[0], "%Y-%m-%d").replace(tzinfo=UTC)
    end_date = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)

    # Fetch BTC prices if needed
    btc_prices: dict[str, float] = {}
    if need_btc_prices:
        btc_prices = _fetch_btc_daily_prices(all_days[0], today)

    # Compute daily trading PnL per currency from non-transfer bills.
    # Transfer = equity_change - trading_pnl (residual method).
    # This avoids double-counting from OKX's dual-leg transfer records.
    daily_pnl_per_ccy: dict[str, dict[str, float]] = {}
    for b in bills:
        sub = str(b.get("sub_type", ""))
        # Skip transfer-type bills (sub_type 11=in, 12=out) — we infer transfers
        if sub in ("11", "12"):
            continue
        ts = int(b.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        day = dt.strftime("%Y-%m-%d")
        ccy = str(b.get("currency", "USDT"))
        change = float(b.get("change", 0) or 0)
        if day not in daily_pnl_per_ccy:
            daily_pnl_per_ccy[day] = {}
        daily_pnl_per_ccy[day][ccy] = daily_pnl_per_ccy[day].get(ccy, 0.0) + change

    # We compute transfers AFTER building the equity curve (need sequential equity values)

    # Build full curve: carry forward per-currency balances
    equity_curve: list[dict[str, str | float]] = []
    last_known: dict[str, float] = {}  # currency -> balance (carry forward)
    d = start_date
    while d <= end_date:
        day_str = d.strftime("%Y-%m-%d")

        # Update known balances for any currencies that have bills this day
        if day_str in daily_balance_per_ccy:
            for ccy, bal in daily_balance_per_ccy[day_str].items():
                last_known[ccy] = bal

        # Compute total equity in USD
        usdt_bal = last_known.get("USDT", 0.0)
        btc_bal = last_known.get("BTC", 0.0)
        btc_usd = btc_prices.get(day_str, btc_prices.get(today, 0.0)) if btc_bal > 0 else 0.0
        btc_val_usd = btc_bal * btc_usd

        total_usd = usdt_bal + btc_val_usd

        point: dict[str, str | float] = {
            "date": day_str,
            "equity_usd": round(total_usd, 2),
        }
        # Include per-currency breakdown
        if usdt_bal > 0:
            point["usdt_balance"] = round(usdt_bal, 2)
        if btc_bal > 0:
            point["btc_balance"] = round(btc_bal, 8)
            point["btc_price_usd"] = round(btc_usd, 2)
            point["btc_value_usd"] = round(btc_val_usd, 2)

        equity_curve.append(point)
        d += timedelta(days=1)

    # Compute transfers using the residual method:
    # transfer = equity_change - trading_pnl_usd
    # This handles OKX's dual-leg transfer records correctly.
    for i in range(len(equity_curve)):
        day_str = str(equity_curve[i]["date"])
        eq_curr = float(equity_curve[i]["equity_usd"])
        eq_prev = float(equity_curve[i - 1]["equity_usd"]) if i > 0 else 0.0

        # Sum trading PnL for this day in USD
        day_pnl = daily_pnl_per_ccy.get(day_str, {})
        usdt_pnl = day_pnl.get("USDT", 0.0)
        btc_pnl = day_pnl.get("BTC", 0.0)
        btc_pnl_usd = 0.0
        if abs(btc_pnl) > 0.0001 and btc_prices:
            price = btc_prices.get(day_str, 0.0)
            if price == 0.0 and btc_prices:
                price = btc_prices.get(sorted(btc_prices.keys())[-1], 0.0)
            btc_pnl_usd = btc_pnl * price
        total_pnl_usd = usdt_pnl + btc_pnl_usd

        if i == 0:
            # First day: entire equity is the initial deposit
            transfer = eq_curr
        else:
            # Residual: equity_change minus trading PnL = cash flow
            equity_change = eq_curr - eq_prev
            # Also account for BTC price change on existing position
            prev_btc_bal = 0.0
            if i > 0:
                prev_btc_bal = float(equity_curve[i - 1].get("btc_balance", 0))
            btc_price_curr = float(equity_curve[i].get("btc_price_usd", 0))
            btc_price_prev = float(equity_curve[i - 1].get("btc_price_usd", 0)) if i > 0 else 0
            price_effect = prev_btc_bal * (btc_price_curr - btc_price_prev) if prev_btc_bal > 0 else 0.0

            transfer = equity_change - total_pnl_usd - price_effect

        # Threshold scales with account size: 1% of equity, clamped [$100, $1000]
        ref_equity = max(abs(eq_curr), abs(eq_prev), 1.0)
        threshold = max(100.0, min(ref_equity * 0.01, 1000.0))
        if abs(transfer) > threshold:
            equity_curve[i]["transfer_usd"] = round(transfer, 2)

    # Override today with current live balances
    if equity_curve:
        usdt_live = 0.0
        btc_live = 0.0
        for ccy, asset_data in current_balance_assets.items():
            total = asset_data.get("total", 0.0) if isinstance(asset_data, dict) else float(asset_data)
            if ccy == "USDT":
                usdt_live = total
            elif ccy == "BTC":
                btc_live = total

        btc_price_today = btc_prices.get(today, 0.0)
        # If we don't have today's price yet, use yesterday's
        if btc_price_today == 0.0 and btc_prices:
            btc_price_today = btc_prices.get(
                sorted(btc_prices.keys())[-1],
                0.0,
            )

        live_total = usdt_live + btc_live * btc_price_today
        equity_curve[-1]["equity_usd"] = round(live_total, 2)
        if usdt_live > 0:
            equity_curve[-1]["usdt_balance"] = round(usdt_live, 2)
        if btc_live > 0:
            equity_curve[-1]["btc_balance"] = round(btc_live, 8)
            equity_curve[-1]["btc_price_usd"] = round(btc_price_today, 2)
            equity_curve[-1]["btc_value_usd"] = round(btc_live * btc_price_today, 2)

    return equity_curve


def compute_daily_equity(
    trades: list[dict[str, str | float | None]],
    transfers: dict[str, list[dict[str, str | float | None]]],
    current_balance: dict[str, float | dict[str, float]],
    income_ledger: list[dict[str, str | float]] | None = None,
    bills_ledger: list[dict[str, str | float]] | None = None,
    venue: str = "",
) -> list[dict[str, str | float]]:
    """Compute daily equity curve using the best available data source.

    Priority:
    1. Binance income ledger (full history, forward replay)
    2. OKX bills ledger (running balance, 3 months + backwards extrapolation)
    3. Fallback: trade fees + current balance (legacy, inaccurate)
    """
    # Compute current total equity
    assets = current_balance.get("assets", {})
    current_total = 0.0
    if isinstance(assets, dict):
        for asset_data in assets.values():
            if isinstance(asset_data, dict):
                current_total += asset_data.get("total", 0.0)
            else:
                current_total += float(asset_data)

    # Method 1: Binance income ledger
    if income_ledger and venue == "binance":
        logger.info("  Using Binance income ledger (%d records)", len(income_ledger))
        return compute_daily_equity_from_binance_income(income_ledger, current_total)

    # Method 2: OKX bills ledger
    if bills_ledger and venue == "okx":
        logger.info("  Using OKX bills ledger (%d records)", len(bills_ledger))
        return compute_daily_equity_from_okx_bills(bills_ledger, transfers, assets if isinstance(assets, dict) else {})

    # Method 3: Legacy fallback (trade fees only — inaccurate)
    logger.warning("  No income/bills ledger — using legacy fee-based reconstruction")
    return _compute_daily_equity_legacy(trades, transfers, current_total)


def _compute_daily_equity_legacy(
    trades: list[dict[str, str | float | None]],
    transfers: dict[str, list[dict[str, str | float | None]]],
    current_total: float,
) -> list[dict[str, str | float]]:
    """Legacy fallback: backward from current balance using fees only."""
    events: list[tuple[str, float]] = []

    for t in trades:
        ts = int(t.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        date_str = dt.strftime("%Y-%m-%d")
        pnl = 0.0
        fee_info = t.get("fee")
        if isinstance(fee_info, dict):
            pnl -= float(fee_info.get("cost", 0) or 0)
        events.append((date_str, pnl))

    for dep in transfers.get("deposits", []):
        ts = int(dep.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        date_str = dt.strftime("%Y-%m-%d")
        amount = float(dep.get("amount", 0) or 0)
        events.append((date_str, amount))

    for wd in transfers.get("withdrawals", []):
        ts = int(wd.get("timestamp", 0) or 0)
        if ts == 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        date_str = dt.strftime("%Y-%m-%d")
        amount = float(wd.get("amount", 0) or 0)
        events.append((date_str, -amount))

    if not events:
        return []

    events.sort(key=lambda x: x[0])
    daily: dict[str, float] = {}
    for date_str, amount in events:
        daily[date_str] = daily.get(date_str, 0.0) + amount

    sorted_dates = sorted(daily.keys())
    if not sorted_dates:
        return []

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    start_date = datetime.strptime(sorted_dates[0], "%Y-%m-%d").replace(tzinfo=UTC)
    end_date = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)

    all_dates: list[str] = []
    d = start_date
    while d <= end_date:
        all_dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    equity = current_total
    reverse_dates = list(reversed(all_dates))
    daily_equities: dict[str, float] = {}
    for date_str in reverse_dates:
        daily_equities[date_str] = equity
        day_change = daily.get(date_str, 0.0)
        equity -= day_change

    equity_curve: list[dict[str, str | float]] = []
    for date_str in all_dates:
        equity_curve.append(
            {
                "date": date_str,
                "equity_usd": round(daily_equities[date_str], 2),
            }
        )
    return equity_curve


def backfill_client(
    client_id: str,
    venue: str,
    currency: str,
    dry_run: bool = False,
    strategy_start_date: str = "",
) -> bool:
    """Backfill all data for a single client."""
    logger.info("=== Backfilling %s (%s/%s) ===", client_id, venue, currency)

    creds = fetch_credentials(client_id, venue)
    if creds is None:
        logger.warning("Skipping %s — no credentials", client_id)
        return False

    if dry_run:
        logger.info("DRY RUN: would fetch trades/transfers for %s", client_id)
        return True

    exchange = create_exchange(
        venue,
        creds["api_key"],
        creds["api_secret"],
        creds["passphrase"],
    )

    # Create output directory
    client_dir = DATA_DIR / client_id
    client_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch current balance + positions
    logger.info("[%s] Fetching current balance...", client_id)
    balance = fetch_balance_snapshot(exchange)
    positions = fetch_positions_snapshot(exchange)

    with open(client_dir / "balance.json", "w") as f:
        json.dump(balance, f, cls=DecimalEncoder, indent=2)
    with open(client_dir / "positions.json", "w") as f:
        json.dump(positions, f, cls=DecimalEncoder, indent=2)

    # 2. Fetch income/bills ledger (primary equity source)
    income_ledger: list[dict[str, str | float]] | None = None
    bills_ledger: list[dict[str, str | float]] | None = None

    if venue == "binance":
        logger.info("[%s] Fetching Binance income ledger...", client_id)
        income_ledger = fetch_binance_income_ledger(exchange)
        with open(client_dir / "income_ledger.json", "w") as f:
            json.dump(income_ledger, f, cls=DecimalEncoder, indent=2)
    elif venue == "okx":
        logger.info("[%s] Fetching OKX bills ledger...", client_id)
        bills_ledger = fetch_okx_bills_ledger(exchange)
        with open(client_dir / "bills_ledger.json", "w") as f:
            json.dump(bills_ledger, f, cls=DecimalEncoder, indent=2)

    # 3. Fetch all trades
    logger.info("[%s] Fetching trade history...", client_id)
    trades = fetch_all_trades_binance(exchange, currency) if venue == "binance" else fetch_all_trades_okx(exchange)

    with open(client_dir / "trades.json", "w") as f:
        json.dump(trades, f, cls=DecimalEncoder, indent=2)
    logger.info("[%s] Saved %d trades", client_id, len(trades))

    # 4. Fetch all transfers
    logger.info("[%s] Fetching transfer history...", client_id)
    transfers = fetch_all_transfers(exchange)

    with open(client_dir / "transfers.json", "w") as f:
        json.dump(transfers, f, cls=DecimalEncoder, indent=2)

    # 5. Compute daily equity curve using ledger data
    logger.info("[%s] Computing equity curve...", client_id)
    equity_curve = compute_daily_equity(
        trades,
        transfers,
        balance,
        income_ledger=income_ledger,
        bills_ledger=bills_ledger,
        venue=venue,
    )

    # Truncate to strategy_start_date if set (ignore data from previous strategies)
    if strategy_start_date and equity_curve:
        pre_len = len(equity_curve)
        equity_curve = [p for p in equity_curve if str(p.get("date", "")) >= strategy_start_date]
        if len(equity_curve) < pre_len:
            logger.info(
                "[%s] Truncated equity curve: %d -> %d days (start: %s)",
                client_id,
                pre_len,
                len(equity_curve),
                strategy_start_date,
            )

    with open(client_dir / "equity_curve.json", "w") as f:
        json.dump(equity_curve, f, cls=DecimalEncoder, indent=2)
    logger.info("[%s] Equity curve: %d days", client_id, len(equity_curve))

    # 6. Summary
    ledger_count = len(income_ledger or bills_ledger or [])
    summary = {
        "client_id": client_id,
        "venue": venue,
        "currency": currency,
        "backfill_timestamp": datetime.now(tz=UTC).isoformat(),
        "trade_count": len(trades),
        "ledger_records": ledger_count,
        "deposit_count": len(transfers["deposits"]),
        "withdrawal_count": len(transfers["withdrawals"]),
        "equity_curve_days": len(equity_curve),
        "equity_source": "binance_income" if income_ledger else ("okx_bills" if bills_ledger else "legacy_fees"),
        "current_equity_usd": balance.get("assets", {}),
        "position_count": len(positions),
    }
    with open(client_dir / "summary.json", "w") as f:
        json.dump(summary, f, cls=DecimalEncoder, indent=2)

    logger.info(
        "[%s] DONE: %d trades, %d ledger records, %d equity days (source: %s)",
        client_id,
        len(trades),
        ledger_count,
        len(equity_curve),
        summary["equity_source"],
    )
    return True


def main() -> None:
    """Run backfill for all or specific clients."""
    parser = argparse.ArgumentParser(description="Backfill historical exchange data")
    parser.add_argument("--client", type=str, help="Specific client ID to backfill")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    registry = load_registry()

    clients_to_process: list[tuple[str, dict[str, str | float | bool | dict[str, float]]]] = []

    for cid, cfg in registry.items():
        if not cfg.get("is_active", False):
            continue
        if cfg.get("tranche") != "managed":
            continue
        if not cfg.get("secret_name"):
            continue
        if args.client and cid != args.client:
            continue
        clients_to_process.append((cid, cfg))

    if not clients_to_process:
        logger.error("No clients to process")
        sys.exit(1)

    logger.info("Will backfill %d client(s)", len(clients_to_process))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, bool] = {}
    for cid, cfg in clients_to_process:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", "USDT"))
        start_date = str(cfg.get("strategy_start_date", ""))
        success = backfill_client(cid, venue, currency, dry_run=args.dry_run, strategy_start_date=start_date)
        results[cid] = success

    # Print summary
    logger.info("\n=== BACKFILL SUMMARY ===")
    for cid, success in results.items():
        status = "OK" if success else "FAILED"
        logger.info("  %s: %s", cid, status)

    failed = sum(1 for s in results.values() if not s)
    if failed:
        logger.warning("%d client(s) failed", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
