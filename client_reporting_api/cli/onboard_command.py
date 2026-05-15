"""``client-reporting-manage onboard`` — add a new client end-to-end.

Validates the caller's read-only credentials, stores them in Secret Manager
(best-effort), registers the client in the credentials registry, and kicks
off a full historical backfill.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import ccxt
import yaml
from unified_trading_library import (  # pyright: ignore[reportPrivateImportUsage]
    create_secret_if_not_exists,
)

from client_reporting_api.cli.shared import (
    DATA_DIR,
    REGISTRY_PATH,
    _create_exchange,
    _get_usd_price,
    _load_full_registry,
)

logger = logging.getLogger(__name__)


def _validate_onboard_credentials(
    venue: str, api_key: str, api_secret: str, passphrase: str
) -> float | None:
    """Open the exchange, fetch balance, return total USD value or None on failure."""
    try:
        exchange = _create_exchange(venue, api_key, api_secret, passphrase)
        balance = exchange.fetch_balance()
    except ccxt.BaseError as exc:
        logger.error("  Invalid credentials: %s", str(exc))
        return None
    total_info = balance.get("total", {})
    skip_keys = {"info", "free", "used", "total", "timestamp", "datetime"}
    total_usd = 0.0
    for cur, val in total_info.items():
        if cur in skip_keys or val is None or float(val) == 0:
            continue
        total_usd += float(val) * _get_usd_price(exchange, cur)
    logger.info("  Credentials valid. Current balance: $%.2f", total_usd)
    return total_usd


def _store_onboard_secrets(
    secret_base: str, venue: str, api_key: str, api_secret: str, passphrase: str
) -> None:
    """Store the client's credentials in Secret Manager (best-effort)."""
    try:
        create_secret_if_not_exists(f"{secret_base}-api-key", api_key)
        create_secret_if_not_exists(f"{secret_base}-api-secret", api_secret)
        if venue == "okx" and passphrase:
            create_secret_if_not_exists(f"{secret_base}-passphrase", passphrase)
        logger.info("  Stored secrets: %s-*", secret_base)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.error("  Failed to store secrets: %s", str(exc))
        logger.info("  You can add them manually to Secret Manager")
        logger.info("  Names: %s-api-key, %s-api-secret", secret_base, secret_base)


def _register_new_client(
    client_id: str,
    full_name: str,
    org_id: str,
    strategy_id: str,
    currency: str,
    venue: str,
    secret_base: str,
    total_usd: float,
) -> None:
    """Add the new client (and its org if needed) to the credentials registry."""
    full_reg = _load_full_registry()
    orgs = full_reg.get("organisations", {})
    if org_id not in orgs:
        orgs[org_id] = {"name": full_name, "type": "client"}
        logger.info("  Added organisation: %s", org_id)

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


def cmd_onboard(args: argparse.Namespace) -> int:
    """Onboard a new client: store credentials, add to registry, run backfill."""
    client_id = args.client_id.upper()
    venue = args.venue.lower()
    passphrase = args.passphrase or ""
    full_name = args.full_name or client_id
    org_id = args.org_id or client_id.lower()
    strategy_id = args.strategy_id or "mean_reversion_top20"
    currency = args.currency or "USDT"

    logger.info("Onboarding client %s on %s...", client_id, venue)

    logger.info("[1/4] Validating API credentials...")
    total_usd = _validate_onboard_credentials(venue, args.api_key, args.api_secret, passphrase)
    if total_usd is None:
        return 1

    if args.dry_run:
        logger.info("[DRY RUN] Would store credentials and add to registry")
        return 0

    logger.info("[2/4] Storing credentials in Secret Manager...")
    secret_base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    _store_onboard_secrets(secret_base, venue, args.api_key, args.api_secret, passphrase)

    logger.info("[3/4] Adding to credentials registry...")
    _register_new_client(
        client_id, full_name, org_id, strategy_id, currency, venue, secret_base, total_usd
    )

    logger.info("[4/4] Running full backfill...")
    from scripts.backfill_history import backfill_client  # noqa: imports-inside-functions

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
