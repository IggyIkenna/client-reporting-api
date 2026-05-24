# pyright: reportPrivateUsage=false
"""CLI entry point for ``client-reporting-manage`` — argparse wiring + dispatch.

Cloud Run Jobs (gen2) require structured JSON logging to stdout for Cloud
Logging. We switch the logging config based on the runtime, sourced from
``ClientReportingApiConfig`` (typed config) — never raw environment reads.
"""

from __future__ import annotations

import argparse
import logging
import sys

from unified_trading_library import (  # pyright: ignore[reportPrivateImportUsage]
    setup_cloud_logging,
)

from client_reporting_api.cli.backfill_command import cmd_backfill
from client_reporting_api.cli.onboard_command import cmd_onboard
from client_reporting_api.cli.status_command import cmd_status
from client_reporting_api.cli.update_command import cmd_update
from client_reporting_api.config import get_config

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure logging based on the runtime context (Cloud Run vs local).

    Cloud Run jobs emit structured JSON via google-cloud-logging; local dev
    uses a plain formatter. The runtime is detected by the typed service
    config (``k_service`` / ``cloud_run_job`` fields on ``UnifiedCloudConfig``),
    never by raw environment reads.
    """
    cfg = get_config()
    in_cloud_run = bool(getattr(cfg, "k_service", "") or getattr(cfg, "cloud_run_job", ""))
    if in_cloud_run:
        # Cloud Run Jobs gen2: structured JSON logs to stdout. google-cloud-logging
        # is installed as part of the service runtime and is the approved path —
        # see unified-trading-library cloud_interface (UnifiedCloudConfig).
        try:
            setup_cloud_logging()
        except (ImportError, RuntimeError, OSError):
            # setup_cloud_logging can fail when the GCP logging SDK is absent
            # (local dev), when ADC isn't available, or on transient socket errors.
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with all sub-commands wired in."""
    parser = argparse.ArgumentParser(
        prog="client-reporting-manage",
        description="Client Reporting Management CLI — full client lifecycle",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_onboard_parser(sub)
    _add_backfill_parser(sub)
    _add_update_parser(sub)
    _add_status_parser(sub)
    return parser


def _add_onboard_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_onboard = sub.add_parser("onboard", help="Add a new client (credentials + registry + backfill)")
    p_onboard.add_argument("--client-id", required=True, help="Client ID (e.g. NEW_CLIENT)")
    p_onboard.add_argument("--venue", required=True, choices=["okx", "binance"], help="Exchange venue")
    p_onboard.add_argument("--api-key", required=True, help="Exchange API key (read-only)")
    p_onboard.add_argument("--api-secret", required=True, help="Exchange API secret")
    p_onboard.add_argument("--passphrase", help="OKX passphrase (if applicable)")
    p_onboard.add_argument("--full-name", help="Client display name")
    p_onboard.add_argument("--org-id", help="Organisation ID (default: client_id)")
    p_onboard.add_argument("--strategy-id", default="mean_reversion_top20", help="Strategy ID")
    p_onboard.add_argument("--currency", default="USDT", help="Account currency (USDT/BTC)")
    p_onboard.add_argument("--dry-run", action="store_true", help="Validate only, don't persist")
    p_onboard.set_defaults(func=cmd_onboard)


def _add_backfill_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_backfill = sub.add_parser("backfill", help="Full historical rebuild from exchange ledger")
    p_backfill.add_argument("--client", help="Specific client ID (default: all)")
    p_backfill.add_argument("--dry-run", action="store_true")
    p_backfill.set_defaults(func=cmd_backfill)


def _add_update_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_update = sub.add_parser("update", help="Incremental refresh (balance, equity, positions, trades, orders)")
    p_update.add_argument("--client", help="Specific client ID (default: all)")
    p_update.add_argument(
        "--full",
        action="store_true",
        help="Full daily snapshot: 7-day trade/order window instead of 24h",
    )
    p_update.add_argument("--dry-run", action="store_true")
    p_update.set_defaults(func=cmd_update)


def _add_status_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_status = sub.add_parser("status", help="Show all clients and data freshness")
    p_status.add_argument("--client", help="Specific client ID")
    p_status.set_defaults(func=cmd_status)


def main() -> None:
    """Entry point wired as ``client-reporting-manage`` in pyproject.toml."""
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
