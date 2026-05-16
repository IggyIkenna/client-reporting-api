"""``client-reporting-manage update`` — hourly incremental refresh.

Orchestrates the per-client update: load existing curve → compute gap →
build today's point → persist balance/positions/trades/orders → sync to GCS.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from client_reporting_api.cli.equity_update import (
    _build_equity_point,
    _compute_update_gap,
    _load_existing_equity_curve,
    _maybe_attach_transfer,
    _merge_today_point_into_curve,
    _open_exchange_with_balance,
    _parse_balances,
    _persist_balance_snapshot,
    _persist_positions,
    _update_summary,
)
from client_reporting_api.cli.gcs_sync import _sync_to_gcs
from client_reporting_api.cli.shared import (
    DATA_DIR,
    DecimalEncoder,
    _get_active_clients,
    _load_registry,
    _print_summary,
)
from client_reporting_api.cli.trades_and_orders import (
    _update_order_history,
    _update_recent_trades,
)

logger = logging.getLogger(__name__)


def _update_client(client_id: str, venue: str, currency: str, dry_run: bool = False, full: bool = False) -> bool:
    """Incremental update for a single client — backfills gaps then updates today."""
    del currency  # currency is registry metadata; not used in this path
    client_dir = DATA_DIR / client_id
    equity_path = client_dir / "equity_curve.json"
    summary_path = client_dir / "summary.json"

    equity_curve = _load_existing_equity_curve(client_id, equity_path, client_dir)
    if not equity_curve:
        if equity_curve is not None:
            logger.warning("[%s] Empty equity curve", client_id)
        return False

    gap = _compute_update_gap(client_id, equity_curve)
    if gap is None:
        return False

    if dry_run:
        logger.info("[%s] DRY RUN: would update (gap=%d days)", client_id, gap.gap_days)
        return True

    opened = _open_exchange_with_balance(client_id, venue)
    if opened is None:
        return False
    exchange, balance_raw = opened

    total_info = balance_raw.get("total", {})
    usdt_bal, btc_bal, btc_price = _parse_balances(exchange, total_info)
    total_usd = usdt_bal + btc_bal * btc_price
    point = _build_equity_point(gap.today, total_usd, usdt_bal, btc_bal, btc_price)
    _maybe_attach_transfer(point, exchange, venue, equity_curve[-1], gap, total_usd, btc_bal, btc_price, client_id)
    equity_curve = _merge_today_point_into_curve(equity_curve, point, gap)

    with open(equity_path, "w") as f:
        json.dump(equity_curve, f, cls=DecimalEncoder, indent=2)

    assets = _persist_balance_snapshot(client_dir, balance_raw, total_info)
    trade_window_hours = 168 if full else 24
    _update_recent_trades(exchange, client_id, client_dir, hours=trade_window_hours)
    _update_order_history(exchange, client_id, venue, client_dir, hours=trade_window_hours)
    _persist_positions(client_dir, exchange, client_id)
    _update_summary(summary_path, equity_curve, assets)

    _sync_to_gcs(client_id, client_dir)

    logger.info(
        "[%s] Updated: equity=$%.2f, %d days, gap=%d",
        client_id,
        total_usd,
        len(equity_curve),
        gap.gap_days,
    )
    return True


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
