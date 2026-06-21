"""Unit tests for the ``daily-ledger-digest`` CLI command (P7.1-C).

Covers ``client_reporting_api.cli.daily_digest_command`` — the scheduled cron
entrypoint (stage C of the paper-week determinism scheduler) that reads a
client's run-ledger and POSTs the daily digest ``AlertEvent``. Asserts: the date
defaults to T+1 (yesterday); an empty ledger is an honest no-op (no POST); a
populated ledger builds + posts; ``--dry-run`` builds but never posts.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P7.1-C).
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from unified_api_contracts import EventOrigin, EventType, LedgerAssetClass, LedgerRow

from client_reporting_api.cli.daily_digest_command import (
    _resolve_digest_date,
    cmd_daily_ledger_digest,
)

_TS = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _trade_row() -> LedgerRow:
    return LedgerRow(
        event_id="evt-1",
        row_id="t1",
        event_origin=EventOrigin.INSTRUCTION,
        event_type=EventType.TRADE,
        timestamp_utc=_TS,
        asset_group="cefi",
        venue="hyperliquid",
        account_id="acct-1",
        client_id="client-A",
        asset_symbol="BTC-PERP",
        asset_canonical_id="btc",
        asset_class=LedgerAssetClass.PERP,
        delta=Decimal("1"),
        price=Decimal("60000"),
        quote_currency="USDT",
        fees_in_quote=Decimal("0"),
    )


def _args(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "client_id": "client-A",
        "date": "2026-05-02",
        "seed_nav": "100000",
        "channel": "#uts-live-alerts",
        "dry_run": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_digest_date_explicit() -> None:
    assert _resolve_digest_date("2026-05-02") == date(2026, 5, 2)


def test_resolve_digest_date_defaults_to_yesterday() -> None:
    today = datetime.now(UTC).date()
    resolved = _resolve_digest_date(None)
    assert resolved == date.fromordinal(today.toordinal() - 1)


def test_empty_ledger_is_honest_noop() -> None:
    with (
        patch(
            "client_reporting_api.cli.daily_digest_command.read_ledger_rows",
            return_value=([], {}),
        ),
        patch("client_reporting_api.cli.daily_digest_command.post_daily_ledger_digest") as mock_post,
    ):
        rc = cmd_daily_ledger_digest(_args())
    assert rc == 0
    mock_post.assert_not_called()


def test_populated_ledger_builds_and_posts() -> None:
    with (
        patch(
            "client_reporting_api.cli.daily_digest_command.read_ledger_rows",
            return_value=([_trade_row()], {"t1": "hyperliquid:PERPETUAL:BTC-PERP"}),
        ),
        patch("client_reporting_api.cli.daily_digest_command.post_daily_ledger_digest") as mock_post,
    ):
        rc = cmd_daily_ledger_digest(_args())
    assert rc == 0
    mock_post.assert_called_once()
    posted_event = mock_post.call_args.args[0]
    assert posted_event.rule_id == "DAILY_LEDGER_DIGEST"
    assert "client-A" in posted_event.message


def test_dry_run_does_not_post() -> None:
    with (
        patch(
            "client_reporting_api.cli.daily_digest_command.read_ledger_rows",
            return_value=([_trade_row()], {"t1": "hyperliquid:PERPETUAL:BTC-PERP"}),
        ),
        patch("client_reporting_api.cli.daily_digest_command.post_daily_ledger_digest") as mock_post,
    ):
        rc = cmd_daily_ledger_digest(_args(dry_run=True))
    assert rc == 0
    mock_post.assert_not_called()
