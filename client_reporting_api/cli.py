"""Client Reporting Management CLI.

Unified entry point for the full client lifecycle:
  - onboard:  Add a new client (registry + Secret Manager + backfill)
  - backfill: Full historical data rebuild from exchange ledger
  - update:   Incremental refresh (balance, equity, positions, trades)
  - status:   Show all clients and data freshness

Usage:
    client-reporting-manage onboard --client-id NEW --venue okx --api-key ... --api-secret ...
    client-reporting-manage backfill --client PR
    client-reporting-manage update                   # all clients
    client-reporting-manage update --client PR       # single client
    client-reporting-manage status

Hourly cron (VM):
    5 * * * * client-reporting-manage update >> /var/log/reporting-update.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os as _os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import ccxt
import yaml

# Cloud Run Jobs (gen2) require structured JSON logging to stdout for Cloud Logging
if _os.environ.get("K_SERVICE") or _os.environ.get("CLOUD_RUN_JOB"):
    import google.cloud.logging as _cloud_logging

    _cloud_logging.Client().setup_logging()
    logger = logging.getLogger(__name__)
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
_APP_DIR = Path("/app")

# Data dir: prefer local dev, fall back to /app (Docker)
_LOCAL_DATA = Path(__file__).resolve().parent.parent / "data" / "backfill"
DATA_DIR = _LOCAL_DATA if _LOCAL_DATA.exists() else _APP_DIR / "data" / "backfill"

# Registry path: workspace (local dev) → /app/configs (Docker) → package-relative
_WORKSPACE_REGISTRY = WORKSPACE / "execution-service" / "configs" / "credentials-registry.yaml"
_DOCKER_REGISTRY = _APP_DIR / "configs" / "credentials-registry.yaml"
_PKG_REGISTRY = Path(__file__).resolve().parent.parent / "configs" / "credentials-registry.yaml"
REGISTRY_PATH = (
    _WORKSPACE_REGISTRY
    if _WORKSPACE_REGISTRY.exists()
    else _DOCKER_REGISTRY
    if _DOCKER_REGISTRY.exists()
    else _PKG_REGISTRY
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class DecimalEncoder(json.JSONEncoder):
    def default(self, o: object) -> float | str:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def _load_registry() -> dict[str, dict[str, str | float | bool | dict[str, float]]]:
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("clients", {})


def _load_full_registry() -> dict[
    str, dict[str, str | float | bool | dict[str, float]] | dict[str, dict[str, str]]
]:
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f)


def _get_secret(name: str) -> str:
    from google.cloud import secretmanager

    project_id = _os.environ.get("GCP_PROJECT_ID", "central-element-323112")
    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{project_id}/secrets/{name}/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("UTF-8")


def _create_exchange(venue: str, api_key: str, api_secret: str, passphrase: str) -> ccxt.Exchange:
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


def _fetch_credentials(client_id: str, venue: str) -> dict[str, str] | None:
    base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    try:
        api_key = _get_secret(f"{base}-api-key")
        api_secret = _get_secret(f"{base}-api-secret")
    except RuntimeError:
        logger.warning("No credentials for %s", base)
        return None
    passphrase = ""
    if venue == "okx":
        try:
            passphrase = _get_secret(f"{base}-passphrase")
        except RuntimeError:
            pass
    return {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase}


def _get_usd_price(exchange: ccxt.Exchange, currency: str) -> float:
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


def _get_active_clients(
    registry: dict[str, dict[str, str | float | bool | dict[str, float]]],
    client_filter: str | None = None,
) -> list[tuple[str, dict[str, str | float | bool | dict[str, float]]]]:
    clients = []
    for cid, cfg in registry.items():
        if (
            not cfg.get("is_active", False)
            or cfg.get("tranche") != "managed"
            or not cfg.get("secret_name")
        ):
            continue
        if client_filter and cid != client_filter:
            continue
        clients.append((cid, cfg))
    return clients


# ---------------------------------------------------------------------------
# ONBOARD — Add a new client end-to-end
# ---------------------------------------------------------------------------


def cmd_onboard(args: argparse.Namespace) -> int:
    """Onboard a new client: store credentials, add to registry, run backfill."""
    client_id = args.client_id.upper()
    venue = args.venue.lower()
    api_key = args.api_key
    api_secret = args.api_secret
    passphrase = args.passphrase or ""
    full_name = args.full_name or client_id
    org_id = args.org_id or client_id.lower()
    strategy_id = args.strategy_id or "mean_reversion_top20"
    currency = args.currency or "USDT"

    logger.info("Onboarding client %s on %s...", client_id, venue)

    # Step 1: Validate credentials by connecting to exchange
    logger.info("[1/4] Validating API credentials...")
    try:
        exchange = _create_exchange(venue, api_key, api_secret, passphrase)
        balance = exchange.fetch_balance()
        total_info = balance.get("total", {})
        skip_keys = {"info", "free", "used", "total", "timestamp", "datetime"}
        total_usd = 0.0
        for cur, val in total_info.items():
            if cur in skip_keys or val is None or float(val) == 0:
                continue
            price = _get_usd_price(exchange, cur)
            total_usd += float(val) * price
        logger.info("  Credentials valid. Current balance: $%.2f", total_usd)
    except ccxt.BaseError as exc:
        logger.error("  Invalid credentials: %s", str(exc))
        return 1

    if args.dry_run:
        logger.info("[DRY RUN] Would store credentials and add to registry")
        return 0

    # Step 2: Store credentials in Secret Manager
    logger.info("[2/4] Storing credentials in Secret Manager...")
    secret_base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    try:
        from unified_trading_library import create_secret

        create_secret(f"{secret_base}-api-key", api_key)
        create_secret(f"{secret_base}-api-secret", api_secret)
        if venue == "okx" and passphrase:
            create_secret(f"{secret_base}-passphrase", passphrase)
        logger.info("  Stored secrets: %s-*", secret_base)
    except Exception as exc:
        logger.error("  Failed to store secrets: %s", str(exc))
        logger.info("  You can add them manually to Secret Manager")
        logger.info("  Names: %s-api-key, %s-api-secret", secret_base, secret_base)

    # Step 3: Add to credentials-registry.yaml
    logger.info("[3/4] Adding to credentials registry...")
    full_reg = _load_full_registry()

    # Add org if not exists
    orgs = full_reg.get("organisations", {})
    if org_id not in orgs:
        orgs[org_id] = {"name": full_name, "type": "client"}
        logger.info("  Added organisation: %s", org_id)

    # Add client
    clients = full_reg.get("clients", {})
    if client_id in clients:
        logger.warning("  Client %s already exists in registry — skipping", client_id)
    else:
        clients[client_id] = {
            "full_name": full_name,
            "organisation_id": org_id,
            "strategy_id": strategy_id,
            "tranche": "managed",
            "currency": currency,
            "venue": venue,
            "secret_name": f"{secret_base}-{currency.lower()}",
            "odum_fee_pct": 0.30,
            "trader_fee_pct": 0.10,
            "is_active": True,
            "strategy_start_date": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
            "initial_deposit_usd": round(total_usd, 2),
        }
        logger.info("  Added client %s to registry", client_id)

    with open(REGISTRY_PATH, "w") as f:
        yaml.dump(full_reg, f, default_flow_style=False, sort_keys=False)

    # Step 4: Run full backfill
    logger.info("[4/4] Running full backfill...")
    from scripts.backfill_history import backfill_client

    client_dir = DATA_DIR / client_id
    client_dir.mkdir(parents=True, exist_ok=True)
    start_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    success = backfill_client(client_id, venue, currency, strategy_start_date=start_date)
    if success:
        logger.info("Onboarding complete! Client %s is now live in the dashboard.", client_id)
    else:
        logger.warning("Backfill failed — client added to registry but needs manual backfill")
        logger.info("  Run: client-reporting-manage backfill --client %s", client_id)

    return 0 if success else 1


# ---------------------------------------------------------------------------
# BACKFILL — Full historical rebuild
# ---------------------------------------------------------------------------


def cmd_backfill(args: argparse.Namespace) -> int:
    """Run full historical backfill from exchange ledger."""
    # Import the existing backfill logic
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backfill_history import backfill_client  # type: ignore[import-not-found]

    registry = _load_registry()
    clients = _get_active_clients(registry, args.client)

    if not clients:
        logger.error("No clients to backfill")
        return 1

    logger.info("Full backfill for %d client(s)", len(clients))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, bool] = {}
    for cid, cfg in clients:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", "USDT"))
        start_date = str(cfg.get("strategy_start_date", ""))
        success = backfill_client(
            cid,
            venue,
            currency,
            dry_run=args.dry_run,
            strategy_start_date=start_date,
        )
        results[cid] = success

    _print_summary("BACKFILL", results)
    return 0 if all(results.values()) else 1


# ---------------------------------------------------------------------------
# UPDATE — Hourly incremental refresh
# ---------------------------------------------------------------------------


def cmd_update(args: argparse.Namespace) -> int:
    """Incremental update: fetch current balance, detect transfers, update equity curve."""
    registry = _load_registry()
    clients = _get_active_clients(registry, args.client)

    if not clients:
        logger.error("No clients to update")
        return 1

    logger.info("Incremental update for %d client(s)", len(clients))
    results: dict[str, bool] = {}

    for cid, cfg in clients:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", "USDT"))
        full = getattr(args, "full", False)
        success = _update_client(cid, venue, currency, dry_run=args.dry_run, full=full)
        results[cid] = success
        time.sleep(0.5)

    _print_summary("UPDATE", results)
    # Return 0 on partial success — Cloud Run Jobs retry on non-zero exit
    succeeded = sum(1 for v in results.values() if v)
    return 0 if succeeded > 0 else 1


def _download_from_gcs(client_id: str, client_dir: Path) -> bool:
    """Download existing client data from GCS if not present locally.

    Cloud Run Jobs start with an empty filesystem — we must bootstrap
    from GCS before doing an incremental update. Also handles any
    environment where local data was lost (machine swap, container restart).
    """
    equity_path = client_dir / "equity_curve.json"
    if equity_path.exists():
        return True  # Already have local data

    bucket_name = _get_gcs_bucket()
    if not bucket_name:
        return False

    try:
        from google.cloud import storage

        gcs_client = storage.Client()
        bucket = gcs_client.bucket(bucket_name)

        client_dir.mkdir(parents=True, exist_ok=True)
        files = [
            "equity_curve.json",
            "orders.json",
            "trades.json",
            "positions.json",
            "balance.json",
            "summary.json",
            "transfers.json",
            "bills_ledger.json",
        ]
        downloaded = 0
        for fname in files:
            blob = bucket.blob(f"backfill/{client_id}/{fname}")
            if blob.exists():
                blob.download_to_filename(str(client_dir / fname))
                downloaded += 1

        if downloaded > 0:
            logger.info("[%s] Downloaded %d files from GCS", client_id, downloaded)
            return equity_path.exists()
        logger.warning("[%s] No data in GCS — needs initial backfill", client_id)
        return False
    except Exception as exc:
        logger.warning("[%s] GCS download failed: %s", client_id, str(exc))
        return False


def _update_client(
    client_id: str, venue: str, currency: str, dry_run: bool = False, full: bool = False
) -> bool:
    """Incremental update for a single client — backfills gaps then updates today."""
    client_dir = DATA_DIR / client_id
    equity_path = client_dir / "equity_curve.json"
    summary_path = client_dir / "summary.json"

    if not equity_path.exists():
        # Try downloading from GCS (Cloud Run Jobs have no local state)
        if not _download_from_gcs(client_id, client_dir):
            logger.warning(
                "[%s] No data locally or in GCS — run: client-reporting-manage backfill --client %s",
                client_id,
                client_id,
            )
            return False

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

    last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=UTC)
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    gap_days = (today_dt - last_dt).days

    if gap_days == 0:
        logger.info("[%s] Same day — refreshing snapshot", client_id)
    else:
        logger.info(
            "[%s] %d day(s) since %s — backfilling to %s", client_id, gap_days, last_date, today
        )

    if dry_run:
        logger.info("[%s] DRY RUN: would update (gap=%d days)", client_id, gap_days)
        return True

    creds = _fetch_credentials(client_id, venue)
    if creds is None:
        return False

    exchange = _create_exchange(venue, creds["api_key"], creds["api_secret"], creds["passphrase"])

    try:
        balance_raw = exchange.fetch_balance()
    except ccxt.BaseError as exc:
        logger.warning("[%s] Failed to fetch balance: %s", client_id, str(exc))
        return False

    # Parse balances
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

    # Build today's equity point
    point: dict[str, str | float] = {"date": today, "equity_usd": round(total_usd, 2)}
    if usdt_bal > 0:
        point["usdt_balance"] = round(usdt_bal, 2)
    if btc_bal > 0:
        point["btc_balance"] = round(btc_bal, 8)
        point["btc_price_usd"] = round(btc_price, 2)
        point["btc_value_usd"] = round(btc_bal * btc_price, 2)

    # Transfer detection via residual method
    if gap_days > 0:
        prev_equity = float(equity_curve[-1].get("equity_usd", 0))
        equity_change = total_usd - prev_equity
        trading_pnl = _fetch_pnl_since(exchange, venue, last_date, today)
        prev_btc = float(equity_curve[-1].get("btc_balance", 0))
        prev_btc_price = float(equity_curve[-1].get("btc_price_usd", 0))
        price_effect = (
            prev_btc * (btc_price - prev_btc_price) if prev_btc > 0 and prev_btc_price > 0 else 0.0
        )
        transfer = equity_change - trading_pnl - price_effect
        ref_equity = max(abs(total_usd), abs(prev_equity), 1.0)
        # USDT-only accounts have no BTC price effect, but residual method
        # still produces noise from ledger PnL query lag. Use higher threshold.
        is_usdt_only = btc_bal == 0
        if is_usdt_only:
            threshold = max(5000.0, ref_equity * 0.02)  # 2% or $5K min
        else:
            threshold = max(100.0, min(ref_equity * 0.01, 1000.0))
        if abs(transfer) > threshold:
            point["transfer_usd"] = round(transfer, 2)
            logger.info("[%s] Detected transfer: $%.2f", client_id, transfer)

    # Fill gap days with carried-forward equity
    if gap_days > 1:
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

    if gap_days > 0:
        equity_curve = [p for p in equity_curve if str(p.get("date", "")) != today]
        equity_curve.append(point)
    else:
        equity_curve[-1] = point

    # Save equity curve
    with open(equity_path, "w") as f:
        json.dump(equity_curve, f, cls=DecimalEncoder, indent=2)

    # Save balance snapshot
    assets: dict[str, dict[str, float]] = {}
    for cur, val in total_info.items():
        if cur in skip_keys or val is None or float(val) == 0:
            continue
        assets[cur] = {
            "total": float(val),
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

    # Recent trades (last 24h, or 7d if --full)
    trade_window_hours = 168 if full else 24  # 7 days for daily snapshot, 24h for hourly
    _update_recent_trades(exchange, client_id, client_dir, hours=trade_window_hours)

    # Order history (MiFID: order lifecycle, best execution data)
    _update_order_history(exchange, client_id, venue, client_dir, hours=trade_window_hours)

    # Positions
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

    # Summary
    summary = {}
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
    summary["last_update"] = datetime.now(tz=UTC).isoformat()
    summary["equity_curve_days"] = len(equity_curve)
    summary["current_equity_usd"] = assets
    with open(summary_path, "w") as f:
        json.dump(summary, f, cls=DecimalEncoder, indent=2)

    # Sync to GCS for durable MiFID-compliant persistence
    _sync_to_gcs(client_id, client_dir)

    logger.info(
        "[%s] Updated: equity=$%.2f, %d days, gap=%d",
        client_id,
        total_usd,
        len(equity_curve),
        gap_days,
    )
    return True


def _fetch_pnl_since(
    exchange: ccxt.Exchange, venue: str, since_date: str, until_date: str
) -> float:
    """Fetch trading PnL between two dates from exchange ledger."""
    since_ms = int(datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime.strptime(until_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms += 86400 * 1000
    total_pnl = 0.0

    if venue == "okx":
        try:
            cursor_ms = since_ms
            while cursor_ms < until_ms:
                end_ms = min(cursor_ms + 7 * 86400 * 1000, until_ms)
                resp = exchange.private_get_account_bills(
                    {"begin": str(cursor_ms), "end": str(end_ms), "limit": "100"}
                )
                bills = resp.get("data", [])
                for bill in bills:
                    if str(bill.get("subType", "")) in ("11", "12"):
                        continue
                    ccy = str(bill.get("ccy", ""))
                    change = float(bill.get("balChg", 0) or 0)
                    if ccy == "USDT":
                        total_pnl += change
                    elif ccy == "BTC":
                        total_pnl += change * _get_usd_price(exchange, "BTC")
                if len(bills) < 100:
                    cursor_ms = end_ms
                else:
                    last_ts = max(int(b.get("ts", 0) or 0) for b in bills)
                    cursor_ms = last_ts + 1 if last_ts > cursor_ms else end_ms
                time.sleep(0.2)
        except ccxt.BaseError as exc:
            logger.warning("Failed to fetch OKX bills: %s", str(exc))

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
            logger.warning("Failed to fetch Binance income: %s", str(exc))

    return total_pnl


def _update_recent_trades(
    exchange: ccxt.Exchange, client_id: str, client_dir: Path, hours: int = 24
) -> None:
    """Append recent trades to trades file."""
    trades_path = client_dir / "trades.json"
    existing: list[dict[str, str | float | None]] = []
    existing_ids: set[str] = set()

    if trades_path.exists():
        with open(trades_path) as f:
            existing = json.load(f)
        existing_ids = {str(t.get("id", "")) for t in existing if t.get("id")}

    since_ms = int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
    new_count = 0

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
                    existing.append(t)
                    existing_ids.add(tid)
                    new_count += 1
        except ccxt.BaseError:
            pass
        time.sleep(0.2)

    if new_count > 0:
        existing.sort(key=lambda t: int(t.get("timestamp", 0) or 0))
        with open(trades_path, "w") as f:
            json.dump(existing, f, cls=DecimalEncoder, indent=2)
        logger.info("[%s] Added %d new trades (total: %d)", client_id, new_count, len(existing))


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
    existing_orders: list[dict[str, str | float | None]] = []
    existing_ids: set[str] = set()

    if orders_path.exists():
        with open(orders_path) as f:
            existing_orders = json.load(f)
        existing_ids = {str(o.get("id", "")) for o in existing_orders if o.get("id")}

    since_ms = int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
    new_count = 0

    # Discover symbols from open positions + core pairs
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
            # OKX: fetch_orders not supported — use closed + open separately
            # Binance: fetch_orders works for all
            raw_orders: list[dict[str, str | float | None]] = []
            # OKX limit=100 max, Binance limit=500
            page_limit = 100 if exchange.id == "okx" else 200
            if exchange.has.get("fetchOrders"):
                raw_orders = exchange.fetch_orders(symbol=sym, since=since_ms, limit=page_limit)
            else:
                closed = exchange.fetch_closed_orders(symbol=sym, since=since_ms, limit=page_limit)
                raw_orders.extend(closed)
                try:
                    open_orders = exchange.fetch_open_orders(
                        symbol=sym, since=since_ms, limit=page_limit
                    )
                    raw_orders.extend(open_orders)
                except ccxt.BaseError:
                    pass
            for order in raw_orders:
                oid = str(order.get("id", ""))
                if oid and oid not in existing_ids:
                    # Extract venue-specific info for cancel/reject reasons
                    info = order.get("info") or {}
                    info_dict = info if isinstance(info, dict) else {}

                    # Cancel/reject reason (MiFID: must track why orders didn't execute)
                    cancel_reason = None
                    if venue == "okx":
                        # OKX: cancelSource (who cancelled), cancelSourceReason (why)
                        src = info_dict.get("cancelSource", "")
                        reason = info_dict.get("cancelSourceReason", "")
                        if src or reason:
                            cancel_reason = f"{src}: {reason}".strip(": ") or None
                    elif venue == "binance":
                        # Binance: status field has CANCELED/EXPIRED/REJECTED
                        raw_status = str(info_dict.get("status", ""))
                        if raw_status in ("CANCELED", "EXPIRED", "REJECTED", "NEW_ADL"):
                            cancel_reason = raw_status

                    # MiFID-relevant fields
                    order_record: dict[str, str | float | None] = {
                        "id": oid,
                        "client_order_id": order.get("clientOrderId"),
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "type": order.get("type"),  # market, limit, stop, etc.
                        "time_in_force": order.get("timeInForce"),  # GTC, IOC, FOK
                        "status": order.get("status"),  # open, closed, canceled
                        "cancel_reason": cancel_reason,  # MiFID: why order didn't fill
                        "amount": float(order.get("amount", 0) or 0),
                        "price": float(order.get("price", 0) or 0) if order.get("price") else None,
                        "average": float(order.get("average", 0) or 0)
                        if order.get("average")
                        else None,
                        "filled": float(order.get("filled", 0) or 0),
                        "remaining": float(order.get("remaining", 0) or 0),
                        "cost": float(order.get("cost", 0) or 0),
                        "fee_cost": float((order.get("fee") or {}).get("cost", 0) or 0),
                        "fee_currency": (order.get("fee") or {}).get("currency"),
                        "timestamp": order.get("timestamp"),  # order creation time
                        "last_update_timestamp": info_dict.get("uTime")
                        or info_dict.get("updateTime"),
                        "last_trade_timestamp": order.get("lastTradeTimestamp"),
                        "venue": venue,
                        "reduce_only": order.get("reduceOnly"),
                        "post_only": order.get("postOnly"),
                        "stop_price": float(order.get("stopPrice", 0) or 0)
                        if order.get("stopPrice")
                        else None,
                        "trigger_price": float(order.get("triggerPrice", 0) or 0)
                        if order.get("triggerPrice")
                        else None,
                    }

                    # Best execution: price improvement = order price vs avg fill price
                    if order_record["price"] and order_record["average"]:
                        order_price = float(order_record["price"])
                        avg_fill = float(order_record["average"])
                        if order_price > 0:
                            slippage_bps = (avg_fill - order_price) / order_price * 10000
                            if order.get("side") == "sell":
                                slippage_bps = -slippage_bps  # Positive = favorable for sells
                            order_record["slippage_bps"] = round(slippage_bps, 2)

                    existing_orders.append(order_record)
                    existing_ids.add(oid)
                    new_count += 1
        except (ccxt.BaseError, ccxt.NotSupported) as exc:
            logger.debug("[%s] Order fetch for %s: %s", client_id, sym, str(exc))
        time.sleep(0.2)

    if new_count > 0:
        existing_orders.sort(key=lambda o: int(o.get("timestamp", 0) or 0))
        with open(orders_path, "w") as f:
            json.dump(existing_orders, f, cls=DecimalEncoder, indent=2)
        logger.info(
            "[%s] Added %d new orders (total: %d)", client_id, new_count, len(existing_orders)
        )


# ---------------------------------------------------------------------------
# GCS persistence — durable storage for MiFID compliance (5-year retention)
# ---------------------------------------------------------------------------

_GCS_BUCKET: str | None = None


def _get_gcs_bucket() -> str | None:
    """Get GCS bucket name for client reporting data."""
    global _GCS_BUCKET
    if _GCS_BUCKET is not None:
        return _GCS_BUCKET if _GCS_BUCKET else None
    try:
        from client_reporting_api.config import get_config

        cfg = get_config()
        if cfg.client_data_bucket:
            _GCS_BUCKET = cfg.client_data_bucket
            logger.info("GCS bucket: %s", _GCS_BUCKET)
            return _GCS_BUCKET
    except Exception as exc:
        logger.warning("Config-based bucket lookup failed: %s", str(exc))
    # Fallback: construct from GCP_PROJECT_ID env var
    project_id = _os.environ.get("GCP_PROJECT_ID", "")
    if project_id:
        _GCS_BUCKET = f"client-reporting-data-{project_id}"
        logger.info("GCS bucket (from env): %s", _GCS_BUCKET)
        return _GCS_BUCKET
    _GCS_BUCKET = ""
    return None


def _sync_to_gcs(client_id: str, client_dir: Path) -> None:
    """Upload client data files to GCS for durable persistence.

    MiFID II requires 5-year retention of order and trade records.
    GCS object versioning provides immutable audit trail.
    Files synced: equity_curve, orders, trades, positions, balance, summary.
    """
    bucket_name = _get_gcs_bucket()
    if not bucket_name:
        return  # Local-only mode (dev), skip GCS sync

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        files_to_sync = [
            "equity_curve.json",
            "orders.json",
            "trades.json",
            "positions.json",
            "balance.json",
            "summary.json",
            "transfers.json",
            "bills_ledger.json",
        ]
        synced = 0
        for fname in files_to_sync:
            fpath = client_dir / fname
            if fpath.exists():
                blob = bucket.blob(f"backfill/{client_id}/{fname}")
                blob.upload_from_filename(str(fpath))
                synced += 1

        if synced > 0:
            logger.debug(
                "[%s] Synced %d files to gs://%s/backfill/%s/",
                client_id,
                synced,
                bucket_name,
                client_id,
            )
    except Exception as exc:
        # GCS sync is best-effort — don't fail the update if GCS is unavailable
        logger.warning("[%s] GCS sync failed (data still local): %s", client_id, str(exc))


# ---------------------------------------------------------------------------
# STATUS — Show all clients and data freshness
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    """Show all clients and their data freshness."""
    registry = _load_registry()
    clients = _get_active_clients(registry, args.client if hasattr(args, "client") else None)

    if not clients:
        logger.info("No active managed clients found")
        return 0

    now = datetime.now(tz=UTC)
    print(
        f"\n{'Client':<12} {'Venue':<8} {'Currency':<8} {'Equity':<14} {'Days':<6} {'Last Update':<22} {'Age':<10}"
    )
    print("-" * 84)

    for cid, cfg in clients:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", ""))
        client_dir = DATA_DIR / cid
        summary_path = client_dir / "summary.json"
        equity_path = client_dir / "equity_curve.json"

        equity_str = "-"
        days_str = "-"
        last_update_str = "no data"
        age_str = "-"

        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)
            last_update = summary.get("last_update", "")
            if last_update:
                last_update_str = last_update[:19]
                try:
                    lu_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
                    age = now - lu_dt
                    if age.total_seconds() < 3600:
                        age_str = f"{int(age.total_seconds() / 60)}m ago"
                    elif age.total_seconds() < 86400:
                        age_str = f"{int(age.total_seconds() / 3600)}h ago"
                    else:
                        age_str = f"{age.days}d ago"
                except ValueError:
                    pass
            days_str = str(summary.get("equity_curve_days", "-"))

        if equity_path.exists():
            with open(equity_path) as f:
                curve = json.load(f)
            if curve:
                last_equity = float(curve[-1].get("equity_usd", 0))
                equity_str = f"${last_equity:,.2f}"

        print(
            f"{cid:<12} {venue:<8} {currency:<8} {equity_str:<14} {days_str:<6} {last_update_str:<22} {age_str:<10}"
        )

    print()
    return 0


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------


def _print_summary(label: str, results: dict[str, bool]) -> None:
    ok = sum(1 for v in results.values() if v)
    fail = sum(1 for v in results.values() if not v)
    logger.info("\n=== %s SUMMARY ===", label)
    for cid, success in results.items():
        logger.info("  %s: %s", cid, "OK" if success else "FAILED")
    logger.info("  %d/%d succeeded", ok, ok + fail)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="client-reporting-manage",
        description="Client Reporting Management CLI — full client lifecycle",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # onboard
    p_onboard = sub.add_parser(
        "onboard", help="Add a new client (credentials + registry + backfill)"
    )
    p_onboard.add_argument("--client-id", required=True, help="Client ID (e.g. NEW_CLIENT)")
    p_onboard.add_argument(
        "--venue", required=True, choices=["okx", "binance"], help="Exchange venue"
    )
    p_onboard.add_argument("--api-key", required=True, help="Exchange API key (read-only)")
    p_onboard.add_argument("--api-secret", required=True, help="Exchange API secret")
    p_onboard.add_argument("--passphrase", help="OKX passphrase (if applicable)")
    p_onboard.add_argument("--full-name", help="Client display name")
    p_onboard.add_argument("--org-id", help="Organisation ID (default: client_id)")
    p_onboard.add_argument("--strategy-id", default="mean_reversion_top20", help="Strategy ID")
    p_onboard.add_argument("--currency", default="USDT", help="Account currency (USDT/BTC)")
    p_onboard.add_argument("--dry-run", action="store_true", help="Validate only, don't persist")
    p_onboard.set_defaults(func=cmd_onboard)

    # backfill
    p_backfill = sub.add_parser("backfill", help="Full historical rebuild from exchange ledger")
    p_backfill.add_argument("--client", help="Specific client ID (default: all)")
    p_backfill.add_argument("--dry-run", action="store_true")
    p_backfill.set_defaults(func=cmd_backfill)

    # update
    p_update = sub.add_parser(
        "update", help="Incremental refresh (balance, equity, positions, trades, orders)"
    )
    p_update.add_argument("--client", help="Specific client ID (default: all)")
    p_update.add_argument(
        "--full",
        action="store_true",
        help="Full daily snapshot: 7-day trade/order window instead of 24h",
    )
    p_update.add_argument("--dry-run", action="store_true")
    p_update.set_defaults(func=cmd_update)

    # status
    p_status = sub.add_parser("status", help="Show all clients and data freshness")
    p_status.add_argument("--client", help="Specific client ID")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
