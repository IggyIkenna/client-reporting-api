"""``client-reporting-manage daily-ledger-digest`` — P7.1-C cron entrypoint.

Daily-T+1 Cloud Run Job stage C of the paper-week determinism scheduler
(``deployment-service/terraform/gcp/paper_week_determinism_scheduler.tf``):
read the client's run-ledger tape for the digest date, fold it through the
operator views + NAV/HWM, and POST the ``DAILY_LEDGER_DIGEST`` ``AlertEvent``
(always INFO) to alerting-service → ``#uts-live-alerts``.

This is the CLI wrapper over the pure ``core.daily_ledger_digest`` helpers
(``build_daily_ledger_digest_event`` + ``post_daily_ledger_digest``, P6.1) and
the GCS ledger reader (``core.ledger_views.read_ledger_rows``). It owns NO new
financial logic — it is the scheduled invocation surface.

SSOT: ``codex/09-strategy/operational/paper-batch-live-reconciliation.md`` §6
Plan: ``plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md`` (P7.1-C)
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from client_reporting_api.cli.shared import _get_active_clients, _load_registry, _print_summary
from client_reporting_api.config import get_config
from client_reporting_api.core.daily_ledger_digest import (
    build_daily_ledger_digest_event,
    post_daily_ledger_digest,
)
from client_reporting_api.core.ledger_views import read_ledger_rows

logger = logging.getLogger(__name__)


def _resolve_digest_date(raw: str | None) -> date:
    """Resolve the digest date — explicit ``--date`` or the prior trading day (T+1)."""
    if raw:
        return date.fromisoformat(raw)
    # T+1 default: yesterday (the day whose book this digest summarises).
    today = datetime.now(UTC).date()
    return date.fromordinal(today.toordinal() - 1)


def _digest_client(
    client_id: str,
    digest_date: date,
    seed_nav: Decimal,
    channel: str,
    dry_run: bool,
) -> bool:
    """Build + POST the daily ledger digest for a single client.

    Reads the client's run-ledger via the GCS seam, builds the INFO digest
    ``AlertEvent`` (balances per venue / trade-tape counts / P&L totals / HWM),
    and POSTs it to alerting-service. Honest no-op when the ledger is empty (no
    fills yet) — never a silent failure, never a fabricated digest. Returns
    ``True`` for every honest outcome (no-op, dry-run, or a real post) — only a
    thrown exception should count as this client's digest failing.
    """
    cfg = get_config()
    rows, instrument_key_by_row_id = read_ledger_rows(client_id, as_of_date=digest_date)
    if not rows:
        logger.info(
            "[daily-ledger-digest] no ledger rows for client=%s date=%s — nothing to digest",
            client_id,
            digest_date.isoformat(),
        )
        return True

    event = build_daily_ledger_digest_event(
        client_id=client_id,
        digest_date=digest_date,
        rows=rows,
        marks={},
        share_class_of={},
        seed_nav=seed_nav,
        channel=channel,
        instrument_key_by_row_id=instrument_key_by_row_id,
    )
    if dry_run:
        logger.info("[daily-ledger-digest] DRY-RUN — would post: %s", event.message)
        return True

    post_daily_ledger_digest(event, alerting_service_url=cfg.alerting_service_url)
    return True


def cmd_daily_ledger_digest(args: argparse.Namespace) -> int:
    """Build + POST the daily ledger digest for every active client (P7.1-C cron stage C).

    Loops over the credentials-registry's active/managed clients (optionally
    narrowed to one via ``--client``) — mirrors ``update``/``backfill``'s
    all-clients-by-default shape so the Cloud Run Job needs no per-client
    parameterisation.
    """
    digest_date = _resolve_digest_date(getattr(args, "date", None))
    seed_nav = Decimal(str(getattr(args, "seed_nav", "0") or "0"))
    channel = str(getattr(args, "channel", "#uts-live-alerts") or "#uts-live-alerts")
    dry_run = bool(getattr(args, "dry_run", False))

    registry = _load_registry()
    clients = _get_active_clients(registry, getattr(args, "client", None))
    if not clients:
        logger.error("No clients to digest")
        return 1

    logger.info("Daily ledger digest for %d client(s), date=%s", len(clients), digest_date.isoformat())
    results: dict[str, bool] = {}
    for cid, _cfg in clients:
        results[cid] = _digest_client(cid, digest_date, seed_nav, channel, dry_run)

    _print_summary("DAILY-LEDGER-DIGEST", results)
    return 0 if all(results.values()) else 1
