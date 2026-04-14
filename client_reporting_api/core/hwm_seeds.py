"""HWM seed values from invoice_state.

Provides historical high-water marks in native units (USD or BTC)
for accounts that had prior history before our equity curve data starts.

Two seed types:
1. Equity HWM seeds — historical peak equity in native units (USD or BTC).
   Used for SL, SL2, ANU, IK.
2. PnL recovery seeds — USDT amount of P&L that must be recovered before
   fees resume. For BTC accounts this is cross-denomination: the USDT amount
   is converted to BTC at the equity curve's starting BTC price.
   Used for GP (hwm_model="pnl_based" in invoice_state).
"""

from __future__ import annotations

# Extracted from invoice_state._HWM_SEED as of 2026-02-17/19
# Values in native account currency (USDT or BTC)
_SEEDS: dict[str, float] = {
    "SL": 650_000.0,
    "SL2": 3.216,
    "ANU": 1.01,
    "IK": 89_000.0,
    # PR, NN, ET, STD, ODUM_PROP: HWM is at or near current equity (no seed needed)
}

# PnL-based recovery seeds — USDT amount to recover before fees resume.
# BTC balance changes are all transfers (no BTC trading); only USDT trading is P&L.
# Recovery = seed - cumulative USDT P&L since tracking_start.
# Extracted from invoice_state GP entry: pnl_recovery_seed=$75K (was $80K, $5K credited Mar 2026)
_PNL_RECOVERY_SEEDS: dict[str, dict[str, float | str]] = {
    "GP": {
        "amount_usd": 75_000.0,
        "tracking_start": "2026-03-02",  # after last USDT sweep (-$4,989.45)
    },
}


def get_hwm_seed(client_id: str) -> float | None:
    """Return historical HWM seed in native units, or None if no seed."""
    return _SEEDS.get(client_id)


def get_pnl_recovery_seed(client_id: str) -> dict[str, float | str] | None:
    """Return PnL recovery seed info, or None if not pnl_based.

    Returns dict with:
        amount_usd: USDT amount to recover before fees resume
        tracking_start: date string from which USDT P&L is tracked
    """
    return _PNL_RECOVERY_SEEDS.get(client_id)
