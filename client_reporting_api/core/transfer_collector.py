# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Collect and normalise transfers from all sources into canonical records.

Sources:
  1. Raw CCXT deposits/withdrawals (data/backfill/{client_id}/transfers.json)
  2. Equity-curve embedded transfer_usd flags (data/backfill/{client_id}/equity_curve.json)
  3. Live CCXT via ExchangeDataCollector.get_client_transfers()

Outputs canonical TransferRecord list per client. Single source of truth
for all downstream consumers (PNL, invoices, tear sheets, dashboards).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from unified_api_contracts.internal import TransferRecord

from client_reporting_api.core.backfill_store import (
    _is_btc_account,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"

# Stablecoins treated as 1:1 with USD
_STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USD"}


def _parse_ccxt_transfer(
    raw: dict[str, str | float | None],
    client_id: str,
    venue: str,
    direction: str,
) -> TransferRecord:
    """Parse raw CCXT deposit/withdrawal record into TransferRecord."""
    ts_val = raw.get("timestamp")
    ts_ms = int(ts_val) if ts_val is not None else 0
    currency = str(raw.get("currency", ""))
    amount = Decimal(str(raw.get("amount", 0) or 0))

    # USD equivalent: stablecoins are 1:1, others unknown from backfill
    usd_amount = amount if currency in _STABLECOINS else Decimal("0")

    fee_info = raw.get("fee")
    fee_cost = Decimal("0")
    if isinstance(fee_info, dict):
        cost_raw = fee_info.get("cost", 0) or 0
        cost_value = float(cost_raw) if cost_raw is not None else 0.0
        fee_cost = Decimal(str(cost_value))

    return TransferRecord(
        transfer_id=str(raw.get("id", "")),
        client_id=client_id,
        venue=venue,
        direction=direction,
        currency=currency,
        amount=amount,
        usd_amount=usd_amount,
        fee=fee_cost,
        status=str(raw.get("status", "ok")),
        timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
        tx_hash=str(raw.get("txid", "") or ""),
        address=str(raw.get("address", "") or ""),
        network=str(raw.get("network", "") or ""),
    )


def collect_from_ccxt_backfill(client_id: str) -> list[TransferRecord]:
    """Extract transfers from raw CCXT backfill data (transfers.json)."""
    path = _DATA_DIR / client_id / "transfers.json"
    if not path.exists():
        return []

    with open(path) as f:
        data = json.load(f)

    records: list[TransferRecord] = []
    venue = _get_venue(client_id)

    for dep in data.get("deposits", []):
        records.append(_parse_ccxt_transfer(dep, client_id, venue, "deposit"))

    for wd in data.get("withdrawals", []):
        records.append(_parse_ccxt_transfer(wd, client_id, venue, "withdrawal"))

    return records


def _get_venue(client_id: str) -> str:
    """Read venue from summary.json for a client."""
    summary_path = _DATA_DIR / client_id / "summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
            return str(summary.get("venue", "unknown"))
    return "unknown"


def _make_record(
    client_id: str,
    suffix: str,
    date: str,
    venue: str,
    direction: str,
    currency: str,
    amount: Decimal,
    usd_amount: Decimal,
) -> TransferRecord:
    """Create a TransferRecord from equity curve data."""
    return TransferRecord(
        transfer_id=f"{client_id}-eq-{suffix}-{date}",
        client_id=client_id,
        venue=venue,
        direction=direction,
        currency=currency,
        amount=amount,
        usd_amount=usd_amount,
        fee=Decimal("0"),
        status="ok",
        timestamp=datetime.strptime(date, "%Y-%m-%d").replace(
            tzinfo=UTC,
        ),
        tx_hash="",
        address="",
        network="",
    )


def _extract_usdt_transfer(
    point: dict[str, str | float],
    client_id: str,
    date: str,
    venue: str,
) -> TransferRecord | None:
    """Extract USDT transfer from equity curve point."""
    transfer_usd = float(point.get("transfer_usd", 0))
    if transfer_usd == 0:
        return None
    direction = "deposit" if transfer_usd > 0 else "withdrawal"
    amount = Decimal(str(abs(transfer_usd)))
    return _make_record(
        client_id,
        "usdt",
        date,
        venue,
        direction,
        "USDT",
        amount,
        amount,
    )


def _extract_btc_transfers(
    point: dict[str, str | float],
    prev_point: dict[str, str | float],
    client_id: str,
    date: str,
    venue: str,
) -> list[TransferRecord]:
    """Extract BTC and USDT transfers from BTC account equity data."""
    if point.get("transfer_usd") is None:
        return []

    records: list[TransferRecord] = []
    # Use Decimal arithmetic to preserve exact balance values from the
    # equity curve — float subtraction produces 0.9999...8-style noise.
    btc_change = Decimal(str(point.get("btc_balance", 0))) - Decimal(str(prev_point.get("btc_balance", 0)))
    usdt_change = Decimal(str(point.get("usdt_balance", 0))) - Decimal(str(prev_point.get("usdt_balance", 0)))
    btc_price = Decimal(str(point.get("btc_price_usd", 1)))

    if abs(btc_change) > Decimal("0.0001"):
        direction = "deposit" if btc_change > 0 else "withdrawal"
        btc_amount = abs(btc_change)
        usd_equiv = (btc_amount * btc_price).quantize(Decimal("0.01"))
        records.append(
            _make_record(
                client_id,
                "btc",
                date,
                venue,
                direction,
                "BTC",
                btc_amount,
                usd_equiv,
            )
        )

    usdt_transfer = usdt_change if abs(usdt_change) > Decimal("500") else Decimal("0")
    if abs(usdt_transfer) > Decimal("500"):
        direction = "deposit" if usdt_transfer > 0 else "withdrawal"
        amt = abs(usdt_transfer)
        records.append(
            _make_record(
                client_id,
                "usdt",
                date,
                venue,
                direction,
                "USDT",
                amt,
                amt,
            )
        )

    return records


def _extract_day1_deposits(
    point: dict[str, str | float],
    client_id: str,
    date: str,
    venue: str,
    is_btc: bool,
) -> list[TransferRecord]:
    """Extract day-1 inception deposit from first equity curve point."""
    day1_xfer = float(point.get("transfer_usd", 0))
    if day1_xfer <= 0:
        return []

    if not is_btc:
        amt = Decimal(str(abs(day1_xfer)))
        return [_make_record(client_id, "usdt", date, venue, "deposit", "USDT", amt, amt)]

    records: list[TransferRecord] = []
    btc_bal = float(point.get("btc_balance", 0))
    usdt_bal = float(point.get("usdt_balance", 0))
    btc_price = float(point.get("btc_price_usd", 1))

    if btc_bal > 0.0001:
        usd_eq = Decimal(str(round(btc_bal * btc_price, 2)))
        records.append(
            _make_record(
                client_id,
                "btc",
                date,
                venue,
                "deposit",
                "BTC",
                Decimal(str(btc_bal)),
                usd_eq,
            )
        )
    if usdt_bal > 1:
        amt = Decimal(str(round(usdt_bal, 2)))
        records.append(
            _make_record(
                client_id,
                "usdt",
                date,
                venue,
                "deposit",
                "USDT",
                amt,
                amt,
            )
        )
    return records


def collect_from_equity_curve(client_id: str) -> list[TransferRecord]:
    """Extract transfers from equity-curve embedded transfer_usd flags.

    This is the primary source for most clients — the equity curve records
    daily snapshots with transfer_usd marking days that had deposits or
    withdrawals. For BTC accounts, detects BTC/USDT balance changes.
    """
    curve_path = _DATA_DIR / client_id / "equity_curve.json"
    if not curve_path.exists():
        return []

    with open(curve_path) as f:
        curve = json.load(f)

    if len(curve) < 2:
        return []

    is_btc = _is_btc_account(curve)
    first_date = str(curve[0].get("date", ""))
    venue = _get_venue(client_id)
    records = _extract_day1_deposits(curve[0], client_id, first_date, venue, is_btc)

    for i in range(1, len(curve)):
        date = str(curve[i].get("date", ""))
        if date == first_date:
            continue

        if not is_btc:
            rec = _extract_usdt_transfer(curve[i], client_id, date, venue)
            if rec is not None:
                records.append(rec)
        else:
            records.extend(
                _extract_btc_transfers(
                    curve[i],
                    curve[i - 1],
                    client_id,
                    date,
                    venue,
                )
            )

    return records


def collect_all_transfers(client_id: str) -> list[TransferRecord]:
    """Collect transfers from all available sources for a client.

    Priority: CCXT raw records (most reliable) > equity curve flags.
    Deduplicates by date + direction + approximate amount.
    """
    ccxt_records = collect_from_ccxt_backfill(client_id)
    eq_records = collect_from_equity_curve(client_id)

    if ccxt_records:
        # CCXT records are authoritative — use them, add equity curve
        # records only for dates not covered by CCXT
        ccxt_dates = {r.timestamp.strftime("%Y-%m-%d") for r in ccxt_records}
        for rec in eq_records:
            date_str = rec.timestamp.strftime("%Y-%m-%d")
            if date_str not in ccxt_dates:
                ccxt_records.append(rec)
        all_records = ccxt_records
    else:
        all_records = eq_records

    all_records.sort(key=lambda r: r.timestamp)
    return all_records
