"""Invoice & HWM state management.

Migrated from mr_report/ manual tracking system (Excel + markdown files).
Hardcoded seed data from February 17, 2026 client snapshots, then overlaid
with live equity from GCS on each read.

Persistence: GCS JSON file per client at
  gs://client-reporting-data-{project_id}/state/{client_id}/hwm_state.json
  gs://client-reporting-data-{project_id}/state/{client_id}/invoices.json
  gs://client-reporting-data-{project_id}/state/{client_id}/payments.json

Until GCS persistence is wired, the hardcoded seed is the SSOT.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from unified_api_contracts.internal import (
    HWMState,
)

from client_reporting_api.core.tranche_router import get_client_config, load_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCS data directory (backfill output from cli.py)
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"


# ---------------------------------------------------------------------------
# Hardcoded seed — from mr_report/clients/*.md as of 2026-02-17
# ---------------------------------------------------------------------------
_SEED_TIMESTAMP = datetime(2026, 2, 17, 0, 0, 0, tzinfo=UTC)

# All values in account currency (USDT for most, BTC for SL2/ANU/GP)
_HWM_SEED: dict[str, dict[str, Decimal | str]] = {
    "PR": {
        "trader_hwm": Decimal("335070"),
        "odum_hwm": Decimal("335070"),  # bumped Apr 9 2026: INV-2026-PR-002 ISSUED ($8,690 PnL)
        "trader_credits": Decimal("0"),
        "trader_paid_since_hwm": Decimal("0"),
        "net_deposits": Decimal("280000"),
        "currency": "USDT",
        "previous_odum_hwm": Decimal("326380"),  # before Apr 9 invoice
        "previous_trader_hwm": Decimal("326380"),
    },
    "NN": {
        "trader_hwm": Decimal("111986"),
        "odum_hwm": Decimal("111986"),  # bumped Apr 9 2026: INV-2026-NN-002 ISSUED ($3,586 PnL)
        "trader_credits": Decimal("0"),
        "trader_paid_since_hwm": Decimal("0"),
        "net_deposits": Decimal("100000"),
        "currency": "USDT",
        "previous_odum_hwm": Decimal("108400"),  # set by INV-2026-NN-001 (Feb, $8,400 PnL, unpaid)
        "previous_trader_hwm": Decimal("108400"),  # equity was $108,400 at Feb 17 payout
    },
    "ET": {
        "trader_hwm": Decimal("2018939"),
        "odum_hwm": Decimal("2018939"),  # bumped Apr 9 2026: INV-2026-ET-002 ISSUED ($18,939 PnL)
        "trader_credits": Decimal("0"),
        "trader_paid_since_hwm": Decimal("0"),
        "net_deposits": Decimal("3462000"),  # 1,981K + 500K + 1M
        "currency": "USDT",
        "previous_odum_hwm": Decimal("519000"),  # before Apr 9 invoice
        "previous_trader_hwm": Decimal("519000"),
    },
    "STD": {
        "trader_hwm": Decimal("1012861"),
        "odum_hwm": Decimal("1012861"),  # bumped Apr 9 2026: INV-2026-STD-002 ISSUED ($12,739 PnL)
        "trader_credits": Decimal("0"),
        "trader_paid_since_hwm": Decimal("0"),
        "net_deposits": Decimal("1458911"),  # 973,589 + 500,122 - 14,800
        "currency": "USDT",
        "previous_odum_hwm": Decimal("514800"),  # before Apr 9 invoice
        "previous_trader_hwm": Decimal("514800"),
    },
    "GP": {
        "trader_hwm": Decimal("0"),
        "odum_hwm": Decimal("0"),
        "trader_credits": Decimal("-1501.70"),
        "refunded_total": Decimal("3888"),
        "currency": "USDT",
        # GP uses PnL-based HWM: recovery = seed - USDT trading PnL since reset
        # BTC balance changes are all transfers (no BTC trading)
        # Only known USDT transfer: -$4,989.45 on Mar 2 (client confirmed)
        "hwm_model": "pnl_based",
        "pnl_reset_date": "2026-02-19",
        "pnl_recovery_seed": Decimal("75000"),  # was 80K, 5K USDT credited Mar 2026
        # after last known USDT sweep; $75K accounts for everything before
        "pnl_tracking_start": "2026-03-02",
    },
    "SL": {
        "trader_hwm": Decimal("650000"),
        "odum_hwm": Decimal("650000"),
        "trader_credits": Decimal("-3949.93"),
        "refunded_total": Decimal("21756.57"),
        "currency": "USDT",
    },
    "SL2": {
        "trader_hwm": Decimal("3.216"),
        "odum_hwm": Decimal("3.216"),
        "trader_credits": Decimal("-1660.70"),
        "currency": "BTC",
    },
    "ANU": {
        "trader_hwm": Decimal("1.01"),
        "odum_hwm": Decimal("1.01"),
        "trader_credits": Decimal("0"),
        "refunded_total": Decimal("1516.90"),
        "currency": "BTC",
    },
    "IK": {
        "trader_hwm": Decimal("89000"),
        "odum_hwm": Decimal("89000"),
        "trader_credits": Decimal("-1241.18"),
        "currency": "USDT",
    },
    "ODUM_PROP": {
        "trader_hwm": Decimal("0"),
        "odum_hwm": Decimal("0"),
        "trader_credits": Decimal("0"),
        "net_deposits": Decimal("0"),
        "currency": "USDT",
    },
}

# Historical invoice records from mr_report payment histories
_INVOICE_SEED: list[dict[str, str | Decimal]] = [
    # PR invoices
    {
        "invoice_id": "INV-2025-019",
        "client_id": "PR",
        "period_month": "2025-11",
        "status": "VOIDED",
        "total": Decimal("10734.79"),
        "notes": "Cancelled — replaced by INV-2025-020",
    },
    {
        "invoice_id": "INV-2025-020",
        "client_id": "PR",
        "period_month": "2025-11",
        "status": "PAID",
        "total": Decimal("10734.79"),
        "notes": "Inception to Nov 1 2025: $26,836.98 returns x 40%. Paid.",
    },
    {
        "invoice_id": "INV-2026-001",
        "client_id": "PR",
        "period_month": "2026-02",
        "status": "PAID",
        "total": Decimal("7817.21"),
        "notes": "Inception to Feb 17 2026: $19,543.02 profit above $306,836.98 HWM x 40%. Paid.",
    },
    # GP refunded invoices
    {
        "invoice_id": "INV-2025-007",
        "client_id": "GP",
        "period_month": "2025-08",
        "status": "VOIDED",
        "total": Decimal("1025.66"),
        "payment_txid": "0x7c3e0456",
        "notes": "Paid then REFUNDED",
    },
    {
        "invoice_id": "INV-2025-017",
        "client_id": "GP",
        "period_month": "2025-09",
        "status": "VOIDED",
        "total": Decimal("2862.34"),
        "payment_txid": "0x3746070d",
        "notes": "Two-part payment ($1,025.66 + $1,836.68), REFUNDED",
    },
    # SL refunded invoices
    {
        "invoice_id": "INV-2025-003",
        "client_id": "SL",
        "period_month": "2025-08",
        "status": "VOIDED",
        "total": Decimal("4803.62"),
        "payment_txid": "0x270cd55e",
        "notes": "Paid then REFUNDED",
    },
    {
        "invoice_id": "INV-2025-011",
        "client_id": "SL",
        "period_month": "2025-09",
        "status": "VOIDED",
        "total": Decimal("16952.95"),
        "payment_txid": "0x076c29e2",
        "notes": "Paid then REFUNDED",
    },
    # ANU refunded invoices
    {
        "invoice_id": "INV-2025-004",
        "client_id": "ANU",
        "period_month": "2025-08",
        "status": "VOIDED",
        "total": Decimal("203.82"),
        "payment_txid": "0x50e2a67f",
        "notes": "Paid then REFUNDED",
    },
    {
        "invoice_id": "INV-2025-016",
        "client_id": "ANU",
        "period_month": "2025-09",
        "status": "VOIDED",
        "total": Decimal("1313.08"),
        "payment_txid": "0x9076e033",
        "notes": "Paid then REFUNDED",
    },
    # STD Invoice — paid
    {
        "invoice_id": "INV-2026-STD-001",
        "client_id": "STD",
        "period_month": "2026-02",
        "status": "PAID",
        "total": Decimal("5180"),
        "notes": "Inception to Feb 17 2026: $14,800 profit above $500K HWM x 35%. Paid.",
    },
    # ET Invoice #25
    {
        "invoice_id": "INV-2026-025",
        "client_id": "ET",
        "period_month": "2026-02",
        "status": "PAID",
        "total": Decimal("5700"),
        "notes": "Inception to Feb 17 2026: $19,000 profit above $500K HWM x 30%. Paid.",
    },
    # Introducer invoice — Max for PR
    {
        "invoice_id": "INT-MAX-001",
        "client_id": "PR",
        "period_month": "2026-02",
        "status": "PAID",
        "total": Decimal("2782.80"),
        "notes": "Introducer fee 15% x $18,552 Odum invoices. Paid Feb 20 2026 (€2,360.76).",
    },
    # NN Feb invoice (sent but unpaid — HWM moved to $108,400)
    {
        "invoice_id": "INV-2026-NN-022",
        "client_id": "NN",
        "period_month": "2026-02",
        "status": "ISSUED",
        "total": Decimal("2520"),
        "notes": "30% x $8,400 PnL above $100,000 HWM. Sent Feb 17 2026. Unpaid.",
    },
    # ── April 9 2026 invoice run — ISSUED (sent, not yet paid) ──
    {
        "invoice_id": "INV-2026-PR-002",
        "client_id": "PR",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("2954.60"),
        "notes": "34% x $8,690 PnL above $326,380 HWM. Sent Apr 9 2026.",
    },
    {
        "invoice_id": "INV-2026-NN-002",
        "client_id": "NN",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("1075.80"),
        "notes": "30% x $3,586 PnL above $108,400 HWM. Sent Apr 9 2026.",
    },
    {
        "invoice_id": "INV-2026-ET-002",
        "client_id": "ET",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("5681.70"),
        "notes": (
            "30% x $18,939 PnL above $519,000 HWM "
            "(deposits $1.5M, withdrawal $19K offset). Sent Apr 9 2026."
        ),
    },
    {
        "invoice_id": "INV-2026-STD-002",
        "client_id": "STD",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("4458.65"),
        "notes": (
            "35% x $12,739 PnL above $514,800 HWM "
            "(deposit $500K, withdrawal $14.8K offset). Sent Apr 9 2026."
        ),
    },
    # Introducer — Max for PR (Apr 9)
    {
        "invoice_id": "INT-MAX-002",
        "client_id": "PR",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("443"),
        "notes": "Introducer fee 15% x $2,955 Odum invoice. Sent Apr 9 2026.",
    },
    # Introducer — Blue Coast for ET (Apr 9)
    {
        "invoice_id": "INT-BC-001",
        "client_id": "ET",
        "period_month": "2026-04",
        "status": "ISSUED",
        "total": Decimal("284"),
        "notes": "Introducer fee 5% x $5,682 Odum invoice. Sent Apr 9 2026.",
    },
]

# Trader payment history from mr_report
_TRADER_PAYMENTS_SEED: list[dict[str, str | Decimal]] = [
    # PR trader payments
    {"client_id": "PR", "date": "2025-09-01", "amount": Decimal("1056.81"), "status": "PAID"},
    {
        "client_id": "PR",
        "date": "2025-11-06",
        "amount": Decimal("1532.00"),
        "status": "PAID",
        "txid": "0x5c1fb941",
    },
    {"client_id": "PR", "date": "2025-11-06", "amount": Decimal("1060.60"), "status": "PAID"},
    # NN trader payments — $507 total (two chunks: $177 + $330)
    {"client_id": "NN", "date": "2025-09-01", "amount": Decimal("177"), "status": "PAID"},
    # ET trader payments
    {"client_id": "ET", "date": "2025-09-01", "amount": Decimal("340.00"), "status": "PAID"},
    # STD trader payments
    {"client_id": "STD", "date": "2025-09-01", "amount": Decimal("340.00"), "status": "PAID"},
    # GP trader payments (NOT refunded — creates credit)
    {"client_id": "GP", "date": "2025-08-01", "amount": Decimal("465.00"), "status": "PAID"},
    {"client_id": "GP", "date": "2025-09-01", "amount": Decimal("1036.70"), "status": "PAID"},
    # SL trader payments (NOT refunded — creates credit)
    {"client_id": "SL", "date": "2025-08-01", "amount": Decimal("1058.30"), "status": "PAID"},
    {"client_id": "SL", "date": "2025-09-01", "amount": Decimal("2891.63"), "status": "PAID"},
    # SL2 trader payments
    {"client_id": "SL2", "date": "2025-09-01", "amount": Decimal("1660.70"), "status": "PAID"},
    # ANU trader payments
    {"client_id": "ANU", "date": "2025-08-01", "amount": Decimal("90.00"), "status": "PAID"},
    {"client_id": "ANU", "date": "2025-09-01", "amount": Decimal("219.90"), "status": "PAID"},
    # IK trader payments
    {"client_id": "IK", "date": "2025-09-01", "amount": Decimal("1241.18"), "status": "PAID"},
    # Feb 2026 pending (from TRADER_PAYMENT_MESSAGE_FEB_2026.md)
    {"client_id": "PR", "date": "2026-02-17", "amount": Decimal("988.60"), "status": "PAID"},
    {"client_id": "NN", "date": "2026-02-17", "amount": Decimal("330.00"), "status": "PAID"},
    {"client_id": "ET", "date": "2026-02-17", "amount": Decimal("1560.00"), "status": "PAID"},
    {"client_id": "STD", "date": "2026-02-17", "amount": Decimal("1140.00"), "status": "PAID"},
    # Apr 9 2026 — ISSUED (sent, not yet paid)
    {"client_id": "PR", "date": "2026-04-09", "amount": Decimal("869"), "status": "ISSUED"},
    {
        "client_id": "NN",
        "date": "2026-04-09",
        "amount": Decimal("359"),
        "status": "ISSUED",
        "notes": "10% x $3,586 PnL from trader HWM $108,400",
    },
    {"client_id": "ET", "date": "2026-04-09", "amount": Decimal("1894"), "status": "ISSUED"},
    {"client_id": "STD", "date": "2026-04-09", "amount": Decimal("1274"), "status": "ISSUED"},
]

# Introducer balances
_INTRODUCER_SEED: dict[str, dict[str, Decimal | str]] = {
    "PR": {
        "introducer_id": "max",
        "introducer_name": "Maxim Shilo",
        "rate_pct": Decimal("0.15"),
        "cumulative_odum_invoiced": Decimal("21507"),  # 18,552 + 2,955 (Apr 9)
        "owed": Decimal("443"),  # INT-MAX-002, ISSUED Apr 9
        "paid": Decimal("2782.80"),  # INT-MAX-001, paid Feb 20 2026, €2,360.76
    },
    "ET": {
        "introducer_id": "bluecoast",
        "introducer_name": "Blue Coast",
        "rate_pct": Decimal("0.05"),
        "cumulative_odum_invoiced": Decimal("11382"),  # 5,700 + 5,682 (Apr 9)
        "owed": Decimal("569"),  # 285 (prev) + 284 (INT-BC-001 Apr 9)
        "paid": Decimal("0"),
        "legacy_paid": Decimal("1250"),
        "legacy_notes": "Old 30% structure (June-Nov 2024), separate from current 5%",
    },
}


# ---------------------------------------------------------------------------
# State Manager — hardcoded first, then GCS overlay
# ---------------------------------------------------------------------------


class InvoiceStateManager:
    """Manages HWM state, invoice history, and trader payments.

    Phase 1 (current): Hardcoded seed data from mr_report.
    Phase 2 (next): Read/write GCS JSON files, seed as fallback.
    Phase 3 (future): Firestore for real-time state with GCS backup.
    """

    def __init__(self) -> None:
        self._hwm_cache: dict[str, HWMState] = {}
        self._live_equity_cache: dict[str, Decimal] = {}

    def get_hwm_state(self, client_id: str) -> HWMState | None:
        """Get current HWM state for a client. Seed data first, GCS overlay later."""
        if client_id in self._hwm_cache:
            return self._hwm_cache[client_id]

        seed = _HWM_SEED.get(client_id)
        if seed is None:
            return None

        trader_hwm = seed["trader_hwm"]
        odum_hwm = seed["odum_hwm"]
        trader_credits = seed["trader_credits"]
        if not isinstance(trader_hwm, Decimal):
            trader_hwm = Decimal(str(trader_hwm))
        if not isinstance(odum_hwm, Decimal):
            odum_hwm = Decimal(str(odum_hwm))
        if not isinstance(trader_credits, Decimal):
            trader_credits = Decimal(str(trader_credits))

        state = HWMState(
            client_id=client_id,
            trader_hwm=trader_hwm,
            odum_hwm=odum_hwm,
            last_updated=_SEED_TIMESTAMP,
            trader_credits=trader_credits,
        )
        self._hwm_cache[client_id] = state
        return state

    def get_all_hwm_states(self) -> dict[str, HWMState]:
        """Get HWM state for all clients with seed data."""
        result: dict[str, HWMState] = {}
        for client_id in _HWM_SEED:
            state = self.get_hwm_state(client_id)
            if state is not None:
                result[client_id] = state
        return result

    def compute_deposits_since_hwm(self, client_id: str) -> Decimal:
        """Compute net deposits/withdrawals since HWM seed date from equity_curve.json.

        Reads transfer_usd entries after _SEED_TIMESTAMP and sums them.
        For BTC share class accounts, converts USD transfers to BTC using
        the BTC price at the time of each transfer.

        This replaces hardcoded deposits_since_hwm values — the system
        computes it automatically from the data on every hourly run.
        """
        equity_path = _DATA_DIR / client_id / "equity_curve.json"
        if not equity_path.exists():
            return Decimal("0")

        try:
            with open(equity_path) as f:
                curve = json.load(f)
        except (json.JSONDecodeError, OSError):
            return Decimal("0")

        seed = _HWM_SEED.get(client_id)
        is_btc_class = seed is not None and seed.get("currency") == "BTC"
        seed_date_str = _SEED_TIMESTAMP.strftime("%Y-%m-%d")

        total = Decimal("0")
        for point in curve:
            date_str = str(point.get("date", ""))
            transfer_usd = point.get("transfer_usd")
            if transfer_usd is not None and date_str > seed_date_str:
                if is_btc_class:
                    # Convert USD transfer to BTC at transfer-date price
                    btc_price = Decimal(str(point.get("btc_price_usd", 0)))
                    if btc_price > 0:
                        total += Decimal(str(transfer_usd)) / btc_price
                else:
                    total += Decimal(str(transfer_usd))
        return total

    def get_live_equity(self, client_id: str) -> Decimal | None:
        """Read latest equity in account currency from GCS backfill data.

        For BTC share class accounts (SL2, ANU, GP-BTC component):
          equity = BTC balance + (USDT balance / BTC price)
          This is denominated in BTC — BTC price appreciation is NOT PnL.

        For USDT share class accounts:
          equity = equity_usd from equity_curve.json
        """
        if client_id in self._live_equity_cache:
            return self._live_equity_cache[client_id]

        equity_path = _DATA_DIR / client_id / "equity_curve.json"
        if not equity_path.exists():
            return None

        try:
            with open(equity_path) as f:
                curve = json.load(f)
            if not curve:
                return None
            last_point = curve[-1]

            # Check if this is a BTC share class account
            seed = _HWM_SEED.get(client_id)
            is_btc_class = seed is not None and seed.get("currency") == "BTC"

            if is_btc_class:
                # BTC share class: equity in BTC terms
                btc_bal = Decimal(str(last_point.get("btc_balance", 0)))
                usdt_bal = Decimal(str(last_point.get("usdt_balance", 0)))
                btc_price = Decimal(str(last_point.get("btc_price_usd", 0)))
                # Convert USDT to BTC equivalent
                usdt_as_btc = usdt_bal / btc_price if btc_price > 0 else Decimal("0")
                result = btc_bal + usdt_as_btc
                self._live_equity_cache[client_id] = result
                return result

            # USDT share class: equity in USD
            equity_usd = last_point.get("equity_usd")
            if equity_usd is not None:
                result = Decimal(str(equity_usd))
                self._live_equity_cache[client_id] = result
                return result
        except (json.JSONDecodeError, OSError, IndexError) as exc:
            logger.warning("Failed to read equity curve for %s: %s", client_id, exc)

        return None

    def get_live_equity_usd(self, client_id: str) -> Decimal | None:
        """Read latest USD equity (for display purposes, not fee computation)."""
        equity_path = _DATA_DIR / client_id / "equity_curve.json"
        if not equity_path.exists():
            return None
        try:
            with open(equity_path) as f:
                curve = json.load(f)
            if not curve:
                return None
            equity_usd = curve[-1].get("equity_usd")
            return Decimal(str(equity_usd)) if equity_usd is not None else None
        except (json.JSONDecodeError, OSError, IndexError):
            return None

    def get_invoices(self, client_id: str | None = None) -> list[dict[str, str | Decimal]]:
        """Get invoice history, optionally filtered by client."""
        if client_id is None:
            return list(_INVOICE_SEED)
        return [inv for inv in _INVOICE_SEED if inv["client_id"] == client_id]

    def get_trader_payments(self, client_id: str | None = None) -> list[dict[str, str | Decimal]]:
        """Get trader payment history, optionally filtered by client."""
        if client_id is None:
            return list(_TRADER_PAYMENTS_SEED)
        return [p for p in _TRADER_PAYMENTS_SEED if p["client_id"] == client_id]

    def get_introducer_state(self, client_id: str) -> dict[str, Decimal | str] | None:
        """Get introducer balance for a client."""
        return _INTRODUCER_SEED.get(client_id)

    def get_total_trader_credits(self) -> Decimal:
        """Total outstanding trader credits across all underwater accounts."""
        total = Decimal("0")
        for seed in _HWM_SEED.values():
            credits = seed.get("trader_credits", Decimal("0"))
            if isinstance(credits, Decimal) and credits < 0:
                total += credits
        return total  # negative = trader owes Odum

    @staticmethod
    def _load_equity_curve(client_id: str) -> list[dict[str, float | str]] | None:
        """Load and parse a client's equity_curve.json, or None on missing/error."""
        equity_path = _DATA_DIR / client_id / "equity_curve.json"
        if not equity_path.exists():
            return None
        try:
            with open(equity_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _net_usdt_moved_after(curve: list[dict[str, float | str]], tracking_start: str) -> Decimal:
        """Sum USDT transfers detected after ``tracking_start``.

        We treat a row as a USDT transfer when ``transfer_usd`` is set AND the
        USDT balance jumps by more than a small noise threshold (>$500) since
        the previous row. BTC-balance changes are recorded but do not affect
        the USDT-only sum returned here.
        """
        net_usdt_moved = Decimal("0")
        for i in range(1, len(curve)):
            date_str = str(curve[i].get("date", ""))
            if date_str <= tracking_start:
                continue
            if curve[i].get("transfer_usd") is None:
                continue
            prev_usdt = Decimal(str(curve[i - 1].get("usdt_balance", 0)))
            curr_usdt = Decimal(str(curve[i].get("usdt_balance", 0)))
            usdt_jump = curr_usdt - prev_usdt
            if abs(usdt_jump) > 500:
                net_usdt_moved += usdt_jump
        return net_usdt_moved

    def _compute_pnl_based_recovery(
        self, client_id: str, seed: dict[str, Decimal | str]
    ) -> Decimal:
        """Recovery for accounts using the PnL-based HWM model (e.g. GP).

        PnL = current USDT balance, offset by any post-``tracking_start`` USDT
        transfers (deposits/withdrawals are not trading PnL). Recovery is the
        residual gap between the recovery seed and that genuine PnL.
        """
        recovery_seed = Decimal(str(seed.get("pnl_recovery_seed", 0)))
        curve = self._load_equity_curve(client_id)
        if not curve:
            return recovery_seed
        current_usdt = Decimal(str(curve[-1].get("usdt_balance", 0)))
        net_usdt_moved = self._net_usdt_moved_after(curve, str(seed.get("pnl_tracking_start", "")))
        usdt_pnl = current_usdt - net_usdt_moved
        return max(Decimal("0"), recovery_seed - usdt_pnl)

    def _compute_recovery(
        self,
        client_id: str,
        live_equity: Decimal,
        hwm: HWMState,
        seed: dict[str, Decimal | str],
    ) -> Decimal:
        """Compute how much recovery is needed for an underwater account.

        GP uses PnL-based model: recovery = seed_amount - trading_pnl_since_reset.
        Others use simple gap: recovery = HWM - current_equity.
        """
        if seed.get("hwm_model") == "pnl_based":
            return self._compute_pnl_based_recovery(client_id, seed)
        return max(Decimal("0"), hwm.odum_hwm - live_equity)

    def _underwater_fee_block(
        self,
        client_id: str,
        cfg: dict[str, object],
        hwm: HWMState,
        seed: dict[str, Decimal | str] | None,
        live_equity: Decimal,
        live_equity_usd: Decimal | None,
        currency: str,
    ) -> dict[str, Decimal | str | bool]:
        """Build the fee summary for an underwater (or prop) account."""
        is_underwater = bool(cfg.get("is_underwater", False))
        result: dict[str, Decimal | str | bool] = {
            "client_id": client_id,
            "currency": currency,
            "current_equity": live_equity,
            "current_equity_usd": live_equity_usd,
            "is_underwater": is_underwater,
            "trader_hwm": hwm.trader_hwm,
            "odum_hwm": hwm.odum_hwm,
            "trader_credits": hwm.trader_credits,
            "server_cost": Decimal("50") if is_underwater else Decimal("0"),
            "trader_fee": Decimal("0"),
            "odum_fee": Decimal("0"),
            "introducer_fee": Decimal("0"),
            "status": "UNDERWATER" if is_underwater else "PROP",
        }
        if is_underwater and seed is not None:
            result["recovery_required"] = self._compute_recovery(client_id, live_equity, hwm, seed)
        return result

    @staticmethod
    def _apply_trader_credits(
        trader_fee: Decimal,
        trader_already_paid: Decimal,
        trader_credits: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Return (net_trader_fee, credit_applied) after deducting paid + credits."""
        net = max(Decimal("0"), trader_fee - trader_already_paid)
        credit_applied = Decimal("0")
        if trader_credits < 0:
            credit_applied = min(net, abs(trader_credits))
            net = net - credit_applied
        return net, credit_applied

    def _compute_at_hwm_fees(
        self,
        client_id: str,
        cfg: dict[str, object],
        hwm: HWMState,
        seed: dict[str, Decimal | str] | None,
        live_equity: Decimal,
    ) -> dict[str, Decimal | str]:
        """Compute the raw fee scalars used by ``_at_hwm_fee_block``."""
        odum_fee_pct = Decimal(str(cfg.get("odum_fee_pct", 0)))
        trader_fee_pct = Decimal(str(cfg.get("trader_fee_pct", 0)))
        introducer_fee_pct = Decimal(str(cfg.get("introducer_fee_pct", 0)))
        introducer_id = str(cfg.get("introducer_id", ""))

        deposits_since = self.compute_deposits_since_hwm(client_id)
        pnl = max(Decimal("0"), live_equity - hwm.odum_hwm - deposits_since)

        odum_fee = pnl * odum_fee_pct
        trader_fee = pnl * trader_fee_pct
        intro_fee = (
            odum_fee * introducer_fee_pct if introducer_fee_pct and introducer_id else Decimal("0")
        )
        trader_already_paid = (
            Decimal(str(seed.get("trader_paid_since_hwm", 0))) if seed else Decimal("0")
        )
        net_trader_fee, credit_applied = self._apply_trader_credits(
            trader_fee, trader_already_paid, hwm.trader_credits
        )
        return {
            "deposits_since": deposits_since,
            "pnl": pnl,
            "odum_fee": odum_fee,
            "trader_fee": trader_fee,
            "intro_fee": intro_fee,
            "trader_already_paid": trader_already_paid,
            "net_trader_fee": net_trader_fee,
            "credit_applied": credit_applied,
            "introducer_id": introducer_id,
        }

    def _at_hwm_fee_block(
        self,
        client_id: str,
        cfg: dict[str, object],
        hwm: HWMState,
        seed: dict[str, Decimal | str] | None,
        live_equity: Decimal,
        live_equity_usd: Decimal | None,
        currency: str,
    ) -> dict[str, Decimal | str | bool]:
        """Build the fee summary for an at-HWM (potentially earning) account."""
        f = self._compute_at_hwm_fees(client_id, cfg, hwm, seed, live_equity)
        pnl = cast(Decimal, f["pnl"])
        return {
            "client_id": client_id,
            "currency": currency,
            "current_equity": live_equity,
            "current_equity_usd": live_equity_usd,
            "is_underwater": False,
            "trader_hwm": hwm.trader_hwm,
            "odum_hwm": hwm.odum_hwm,
            "effective_hwm": hwm.odum_hwm + cast(Decimal, f["deposits_since"]),
            "deposits_since_hwm": f["deposits_since"],
            "pnl": pnl,
            "pnl_above_trader_hwm": pnl,
            "pnl_above_odum_hwm": pnl,
            "trader_fee_gross": f["trader_fee"],
            "trader_already_paid": f["trader_already_paid"],
            "trader_credits": hwm.trader_credits,
            "credit_applied": f["credit_applied"],
            "trader_fee_net": f["net_trader_fee"],
            "odum_fee": f["odum_fee"],
            "introducer_id": f["introducer_id"],
            "introducer_fee": f["intro_fee"],
            "server_cost": Decimal("0"),
            "total_due": cast(Decimal, f["odum_fee"]) + cast(Decimal, f["intro_fee"]),
            "status": "AT_HWM" if pnl > 0 else "BELOW_HWM",
        }

    def compute_current_fees(self, client_id: str) -> dict[str, Decimal | str | bool] | None:
        """Compute current fee position using seed HWM + live equity (the trickle).

        Returns fee breakdown or None if client not found.
        """
        hwm = self.get_hwm_state(client_id)
        cfg = get_client_config(client_id)
        if hwm is None or cfg is None:
            return None

        seed = _HWM_SEED.get(client_id)
        currency = str(seed.get("currency", "USDT")) if seed else "USDT"
        live_equity = self.get_live_equity(client_id)
        live_equity_usd = self.get_live_equity_usd(client_id)

        if live_equity is None:
            return {
                "client_id": client_id,
                "currency": currency,
                "status": "NO_LIVE_DATA",
                "is_underwater": bool(cfg.get("is_underwater", False)),
                "trader_hwm": hwm.trader_hwm,
                "odum_hwm": hwm.odum_hwm,
                "trader_credits": hwm.trader_credits,
            }

        if cfg.get("is_underwater", False) or cfg.get("odum_fee_pct", 0) == 0:
            return self._underwater_fee_block(
                client_id, cfg, hwm, seed, live_equity, live_equity_usd, currency
            )
        return self._at_hwm_fee_block(
            client_id, cfg, hwm, seed, live_equity, live_equity_usd, currency
        )

    @staticmethod
    def _fund_of_fund_entry(
        client_id: str, cfg: dict[str, object]
    ) -> dict[str, Decimal | str | bool]:
        """Build the dashboard entry for a fund-of-fund client (manual data)."""
        return {
            "client_id": client_id,
            "full_name": str(cfg.get("full_name", "")),
            "strategy_id": str(cfg.get("strategy_id", "")),
            "data_source": "manual",
            "status": "FUND_OF_FUND",
        }

    def _classify_client_for_dashboard(
        self,
        client_id: str,
        cfg: dict[str, object],
        buckets: dict[str, list[dict[str, Decimal | str | bool]]],
    ) -> None:
        """Compute fees + place a single client into the correct dashboard bucket."""
        fees = self.compute_current_fees(client_id)
        if fees is None:
            return
        fees["full_name"] = str(cfg.get("full_name", ""))
        fees["venue"] = str(cfg.get("venue", ""))
        fees["currency"] = str(cfg.get("currency", ""))
        status = str(fees.get("status", ""))
        if status == "PROP":
            buckets["prop"].append(fees)
        elif status == "UNDERWATER":
            buckets["underwater"].append(fees)
        else:
            buckets["at_hwm"].append(fees)

    def get_dashboard_summary(self) -> dict[str, list[dict[str, Decimal | str | bool]]]:
        """Get full dashboard: all clients with fees, grouped by status."""
        registry = load_registry()
        clients = registry.get("clients", {})

        buckets: dict[str, list[dict[str, Decimal | str | bool]]] = {
            "at_hwm": [],
            "underwater": [],
            "fund_of_fund": [],
            "prop": [],
        }

        for client_id, cfg in clients.items():
            if not cfg.get("is_active", False):
                continue
            if cfg.get("tranche", "") == "fund_of_fund":
                buckets["fund_of_fund"].append(self._fund_of_fund_entry(client_id, cfg))
                continue
            self._classify_client_for_dashboard(client_id, cfg, buckets)

        underwater_count = len(buckets["underwater"])
        at_hwm_count = len(buckets["at_hwm"])
        return {
            **buckets,
            "totals": [
                {
                    "total_trader_credits": self.get_total_trader_credits(),
                    "total_server_costs_monthly": Decimal("50") * underwater_count,
                    "underwater_count": Decimal(str(underwater_count)),
                    "at_hwm_count": Decimal(str(at_hwm_count)),
                },
            ],
        }
