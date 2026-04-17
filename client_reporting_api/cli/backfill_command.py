"""``client-reporting-manage backfill`` — full historical rebuild from exchange ledger."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from client_reporting_api.cli.shared import (
    DATA_DIR,
    _get_active_clients,
    _load_registry,
    _print_summary,
)

logger = logging.getLogger(__name__)


def cmd_backfill(args: argparse.Namespace) -> int:
    """Run full historical backfill from exchange ledger."""
    # Import the existing backfill logic (lives in scripts/, outside the package)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
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
