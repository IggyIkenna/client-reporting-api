"""Daily ledger digest → AlertEvent(INFO) → alerting-service (P6.1).

The Citadel determinism-spine operator surface (``paper-batch-live-reconciliation.md``
§6) calls for a DAILY Slack digest of a paper/live run's as-if-filled ledger
state: balances per venue / instrument / share_class, the day's InstructionLedger
trade tape summary, realised + unrealised P&L, and the HWM. This module folds the
already-computed ledger views (``core.ledger_views.compute_ledger_views``) + the
NAV/HWM series (``core.hwm_from_ledger``) into a single ``AlertEvent`` (always
INFO — it is an informational eyeball surface, never an alert) and POSTs it to
alerting-service over HTTP. Alerting-service routes it to ``#uts-live-alerts``.

This is the COMPANION to the T+1 recon verdict digest (P6.2, the
``BATCH_VS_LIVE_RECON_DRIFTED`` AlertEvent emitted by batch-live-reconciliation-service):
the recon digest proves paper≡batch; this digest shows the day's book.

Typed HTTP client (httpx) — NO cross-service Python import of alerting-service
(the T4 service↔service ban). The contract is the UAC ``AlertEvent`` schema.

SSOT: ``codex/09-strategy/operational/paper-batch-live-reconciliation.md`` §6
Plan: ``plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md`` (P6.1)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

import httpx
from unified_api_contracts import LedgerRow
from unified_api_contracts.alerting import AlertCode
from unified_api_contracts.internal import AlertEvent

from client_reporting_api.core.hwm_from_ledger import hwm_from_ledger, ledger_nav_series
from client_reporting_api.core.ledger_views import compute_ledger_views

logger = logging.getLogger(__name__)

_ALERTS_INGRESS_PATH = "/api/v1/alerts/rules/recent"
_RULE_ID = "DAILY_LEDGER_DIGEST"
_POST_TIMEOUT_SECONDS = 15
_Severity = Literal["DEBUG", "INFO", "WARNING", "CRITICAL", "FATAL"]


def _trade_tape_summary(rows: Sequence[LedgerRow]) -> tuple[int, int]:
    """Return (trade_count, passive_accrual_count) for the day's tape."""
    trades = sum(1 for r in rows if r.event_type.value == "trade")
    passive = sum(1 for r in rows if r.event_origin.value == "passive")
    return trades, passive


def build_daily_ledger_digest_event(
    *,
    client_id: str,
    digest_date: date,
    rows: Sequence[LedgerRow],
    marks: Mapping[str, Decimal],
    share_class_of: Mapping[str, str],
    seed_nav: Decimal,
    channel: str,
) -> AlertEvent:
    """Build the daily ledger-digest ``AlertEvent`` (always INFO).

    Folds the client's ledger tape into the operator views + HWM and packs a
    concise digest message (per-venue balances, trade-tape counts, P&L totals,
    HWM peak). The ``metric_value`` is the day's total P&L.

    Args:
        client_id: the client whose book this digest covers.
        digest_date: the trading day the digest summarises.
        rows: the client's ``LedgerRow`` tape for the day (TRADE + PASSIVE).
        marks: ``asset_canonical_id -> mark price``.
        share_class_of: ``asset_canonical_id -> share_class``.
        seed_nav: the NAV seed (starting equity) for the HWM series.
        channel: Slack channel target (surfaced for alerting-service routing).
    """
    as_of = datetime.combine(digest_date, datetime.min.time(), tzinfo=UTC)
    views = compute_ledger_views(rows, marks=marks, as_of=as_of, share_class_of=share_class_of)
    totals = cast(dict[str, str], views["totals"])
    balances = cast(dict[str, object], views["balances"])
    by_venue = cast("list[dict[str, object]]", balances.get("by_venue", []))

    # NAV/HWM off the materialised ledger (advances-only; never max-equity).
    # The digest's HWM period is the single trading day [as_of, as_of + 1 day).
    nav_series = ledger_nav_series([(as_of, views)], seed=seed_nav)
    hwm_rows = hwm_from_ledger(
        nav_series,
        prior_peak_nav=seed_nav,
        client_id=client_id,
        share_class_id="ALL",
        period_start=as_of,
        period_end=as_of + timedelta(days=1),
    )
    hwm_peak: Decimal = max((r.prior_peak_nav + r.delta for r in hwm_rows), default=seed_nav)

    trades, passive = _trade_tape_summary(rows)
    total_pnl = Decimal(totals.get("total_pnl", "0"))

    venue_summary = (
        ", ".join(f"{b.get('venue')}=net_qty {b.get('net_qty')}" for b in by_venue) or "(no venues)"
    )
    message = (
        f"[{channel}] DAILY LEDGER DIGEST — client {client_id} [{digest_date.isoformat()}]: "
        f"{trades} trades + {passive} accruals booked. "
        f"P&L realised={totals.get('realized_pnl', '0')} unrealised={totals.get('unrealized_pnl', '0')} "
        f"total={totals.get('total_pnl', '0')}. HWM peak={hwm_peak}. "
        f"balances by venue: {venue_summary}."
    )
    severity: _Severity = "INFO"
    return AlertEvent(
        alert_id=str(uuid.uuid4()),
        rule_id=_RULE_ID,
        triggered_at=datetime.now(UTC),
        severity=severity,
        message=message,
        metric_value=float(total_pnl),
        threshold=0.0,
        code=AlertCode.DAILY_LEDGER_DIGEST,
    )


def post_daily_ledger_digest(
    event: AlertEvent,
    *,
    alerting_service_url: str,
) -> AlertEvent | None:
    """POST the daily ledger digest ``AlertEvent`` to alerting-service over HTTP.

    Args:
        event: the digest event (built by :func:`build_daily_ledger_digest_event`).
        alerting_service_url: base URL; empty → no POST (logged only).

    Returns:
        The posted event on success, else ``None`` (no URL configured).
    """
    if not alerting_service_url:
        logger.warning("[daily-ledger-digest] alerting_service_url unset — digest NOT posted: %s", event.message)
        return None
    url = f"{alerting_service_url.rstrip('/')}{_ALERTS_INGRESS_PATH}"
    payload = event.model_dump(mode="json")
    resp = httpx.post(url, json=payload, timeout=_POST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    logger.info("[daily-ledger-digest] posted INFO digest to %s", url)
    return event


__all__ = [
    "build_daily_ledger_digest_event",
    "post_daily_ledger_digest",
]
