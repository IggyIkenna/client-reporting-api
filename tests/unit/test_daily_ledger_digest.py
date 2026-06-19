"""Unit tests for the daily ledger digest AlertEvent builder (P6.1).

Covers ``client_reporting_api.core.daily_ledger_digest`` — the INFO Slack digest
of a paper/live run's as-if-filled ledger state (balances per venue, trade-tape
counts, P&L totals, HWM) that POSTs to alerting-service. Asserts the event is
always INFO, carries the DAILY_LEDGER_DIGEST code, and the message reflects the
booked tape (trade count + P&L).

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P6.1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from unified_api_contracts import EventOrigin, EventType, LedgerAssetClass, LedgerRow
from unified_api_contracts.alerting import AlertCode

from client_reporting_api.core.daily_ledger_digest import build_daily_ledger_digest_event

_TS = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _trade_row(row_id: str, delta: Decimal, price: Decimal) -> LedgerRow:
    return LedgerRow(
        event_id=f"evt-{row_id}",
        row_id=row_id,
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
        delta=delta,
        price=price,
        quote_currency="USDT",
        fees_in_quote=Decimal("0"),
    )


def test_digest_event_is_info_with_correct_code() -> None:
    rows = [_trade_row("t1", Decimal("1"), Decimal("60000")), _trade_row("t2", Decimal("-0.4"), Decimal("65000"))]
    event = build_daily_ledger_digest_event(
        client_id="client-A",
        digest_date=date(2026, 5, 2),
        rows=rows,
        marks={"btc": Decimal("64000")},
        share_class_of={"btc": "USDT"},
        seed_nav=Decimal("100000"),
        channel="#uts-live-alerts",
    )
    assert event.severity == "INFO"
    assert event.code is AlertCode.DAILY_LEDGER_DIGEST
    assert event.rule_id == "DAILY_LEDGER_DIGEST"
    assert "client-A" in event.message
    assert "2026-05-02" in event.message
    assert "2 trades" in event.message
    assert "hyperliquid" in event.message


def test_digest_empty_tape_is_honest_zero() -> None:
    event = build_daily_ledger_digest_event(
        client_id="client-A",
        digest_date=date(2026, 5, 2),
        rows=[],
        marks={},
        share_class_of={},
        seed_nav=Decimal("0"),
        channel="#uts-live-alerts",
    )
    assert event.severity == "INFO"
    assert "0 trades" in event.message
    assert event.metric_value == 0.0


def test_digest_counts_passive_accruals_separately() -> None:
    rows = [
        _trade_row("t1", Decimal("1"), Decimal("60000")),
        LedgerRow(
            event_id="evt-f1",
            row_id="f1",
            event_origin=EventOrigin.PASSIVE,
            event_type=EventType.FUNDING_ACCRUAL,
            timestamp_utc=_TS,
            asset_group="cefi",
            venue="hyperliquid",
            account_id="acct-1",
            client_id="client-A",
            asset_symbol="BTC-PERP",
            asset_canonical_id="btc",
            asset_class=LedgerAssetClass.PERP,
            delta=Decimal("12.5"),
            price=Decimal("1"),
            quote_currency="USDT",
            fees_in_quote=Decimal("0"),
        ),
    ]
    event = build_daily_ledger_digest_event(
        client_id="client-A",
        digest_date=date(2026, 5, 2),
        rows=rows,
        marks={"btc": Decimal("60000")},
        share_class_of={"btc": "USDT"},
        seed_nav=Decimal("100000"),
        channel="#uts-live-alerts",
    )
    assert "1 trades + 1 accruals" in event.message
