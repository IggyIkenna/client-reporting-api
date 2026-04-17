"""Read-only store for backfilled historical data.

Loads JSON files from data/backfill/{client_id}/ written by
scripts/backfill_history.py. Returns None when no backfill data exists.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"


def _load_json(client_id: str, filename: str) -> list[dict[str, str | float]] | None:
    """Load a JSON file for a client, return None if missing."""
    path = _DATA_DIR / client_id / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def get_equity_curve(client_id: str) -> list[dict[str, str | float]]:
    """Return backfilled daily equity curve for a client."""
    data = _load_json(client_id, "equity_curve.json")
    return data if data is not None else []


def get_backfill_trades(client_id: str) -> list[dict[str, str | float | None]]:
    """Return backfilled trade history for a client."""
    data = _load_json(client_id, "trades.json")
    return data if data is not None else []


def get_backfill_transfers(client_id: str) -> dict[str, list[dict[str, str | float | None]]]:
    """Return backfilled deposits/withdrawals for a client."""
    path = _DATA_DIR / client_id / "transfers.json"
    if not path.exists():
        return {"deposits": [], "withdrawals": []}
    with open(path) as f:
        return json.load(f)


def get_backfill_summary(client_id: str) -> dict[str, str | int | float] | None:
    """Return backfill summary metadata for a client."""
    path = _DATA_DIR / client_id / "summary.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _is_btc_account(equity_curve: list[dict[str, str | float]]) -> bool:
    """Detect if this is a BTC-denominated account from equity curve fields."""
    if not equity_curve:
        return False
    sample = equity_curve[0]
    return "btc_balance" in sample and "btc_price_usd" in sample


def _btc_equity(point: dict[str, str | float]) -> float:
    """Compute BTC-denominated equity: btc_balance + usdt_balance / btc_price."""
    btc = float(point.get("btc_balance", 0))
    usdt = float(point.get("usdt_balance", 0))
    btc_price = float(point.get("btc_price_usd", 1))
    return btc + (usdt / btc_price if btc_price > 0 else 0)


def _get_equity(point: dict[str, str | float], is_btc: bool) -> float:
    """Get equity in native units (BTC for BTC accounts, USD for USDT accounts)."""
    if is_btc:
        return _btc_equity(point)
    return float(point.get("equity_usd", 0))


def _get_transfer(
    point: dict[str, str | float],
    prev_point: dict[str, str | float],
    is_btc: bool,
    first_date: str,
) -> float:
    """Get transfer amount in native units for the day.

    For USDT accounts: uses transfer_usd field.
    For BTC accounts: detects BTC balance changes and large USDT jumps
    on days flagged with transfer_usd, converting to BTC equivalent.
    """
    date = str(point.get("date", ""))
    if date == first_date:
        return 0.0

    if not is_btc:
        return float(point.get("transfer_usd", 0))

    # BTC account: only process on days with transfer_usd flag
    if point.get("transfer_usd") is None:
        return 0.0

    btc_bal = float(point.get("btc_balance", 0))
    prev_btc = float(prev_point.get("btc_balance", 0))
    usdt_bal = float(point.get("usdt_balance", 0))
    prev_usdt = float(prev_point.get("usdt_balance", 0))
    btc_price = float(point.get("btc_price_usd", 1))

    btc_change = btc_bal - prev_btc
    usdt_change = usdt_bal - prev_usdt
    usdt_transfer = usdt_change if abs(usdt_change) > 500 else 0.0

    transfer_btc = btc_change
    if usdt_transfer != 0 and btc_price > 0:
        transfer_btc += usdt_transfer / btc_price

    return transfer_btc


def compute_monthly_returns(
    equity_curve: list[dict[str, str | float]],
) -> list[dict[str, str | float]]:
    """Compute monthly TWR returns from daily equity curve.

    Handles both BTC and USDT denominated accounts. Uses transfer-adjusted
    daily returns compounded within each month.
    """
    if len(equity_curve) < 2:
        return []

    is_btc = _is_btc_account(equity_curve)
    first_date = str(equity_curve[0].get("date", ""))

    # Group daily returns by month
    monthly_returns: dict[str, float] = {}

    for i in range(1, len(equity_curve)):
        eq_prev = _get_equity(equity_curve[i - 1], is_btc)
        eq_curr = _get_equity(equity_curve[i], is_btc)
        transfer = _get_transfer(equity_curve[i], equity_curve[i - 1], is_btc, first_date)

        if eq_prev <= 0 or eq_curr <= 0:
            continue

        day_return = (eq_curr - transfer) / eq_prev - 1.0
        month = str(equity_curve[i].get("date", ""))[:7]

        if month not in monthly_returns:
            monthly_returns[month] = 1.0
        monthly_returns[month] *= 1.0 + day_return

    returns: list[dict[str, str | float]] = []
    for month in sorted(monthly_returns.keys()):
        ret_pct = (monthly_returns[month] - 1.0) * 100
        returns.append({"month": month, "return_pct": round(ret_pct, 2)})

    return returns


def _compute_daily_returns(
    equity_curve: list[dict[str, str | float]], is_btc: bool, first_date: str
) -> tuple[list[float], list[dict[str, str | float]]]:
    """Return (per-day transfer-adjusted returns, days flagged as suspicious).

    A day is "flagged" when ``|return| > 25%`` — often caused by a missing
    transfer event on either side of the interval.
    """
    daily_returns: list[float] = []
    flagged_days: list[dict[str, str | float]] = []
    for i in range(1, len(equity_curve)):
        eq_prev = _get_equity(equity_curve[i - 1], is_btc)
        eq_curr = _get_equity(equity_curve[i], is_btc)
        if eq_prev <= 0 or eq_curr <= 0:
            continue
        transfer = _get_transfer(equity_curve[i], equity_curve[i - 1], is_btc, first_date)
        day_return = (eq_curr - transfer) / eq_prev - 1.0
        if abs(day_return) > 0.25:
            flagged_days.append(
                {
                    "date": str(equity_curve[i].get("date", "")),
                    "daily_return_pct": round(day_return * 100, 2),
                    "equity": round(eq_curr, 6) if is_btc else round(eq_curr, 2),
                    "prev_equity": round(eq_prev, 6) if is_btc else round(eq_prev, 2),
                    "detected_transfer": round(transfer, 6) if is_btc else round(transfer, 2),
                }
            )
        daily_returns.append(day_return)
    return daily_returns, flagged_days


def _twr_equity_series(daily_returns: list[float]) -> list[float]:
    """Build the chain-linked TWR equity series starting at 1.0."""
    twr_equity = [1.0]
    for dr in daily_returns:
        twr_equity.append(twr_equity[-1] * (1.0 + dr))
    return twr_equity


def _max_drawdown_stats(twr_equity: list[float]) -> tuple[float, int]:
    """Return (max drawdown fraction, longest drawdown duration in days)."""
    peak = twr_equity[0] if twr_equity else 1.0
    max_dd = 0.0
    max_dd_duration = 0
    in_dd = False
    current_dd_start = 0
    for idx, eq in enumerate(twr_equity):
        if eq >= peak:
            peak = eq
            if in_dd:
                duration = idx - current_dd_start
                if duration > max_dd_duration:
                    max_dd_duration = duration
                in_dd = False
        else:
            if not in_dd:
                in_dd = True
                current_dd_start = idx
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
    if in_dd:
        duration = len(twr_equity) - 1 - current_dd_start
        if duration > max_dd_duration:
            max_dd_duration = duration
    return max_dd, max_dd_duration


def _risk_ratios(daily_returns: list[float]) -> tuple[float, float, float]:
    """Return (annualised Sharpe, annualised Sortino, annualised volatility pct)."""
    if not daily_returns:
        return 0.0, 0.0, 0.0
    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std_ret = variance**0.5
    sharpe = (mean_ret / std_ret) * (365**0.5) if std_ret > 0 else 0.0

    downside_var = sum(min(0, r) ** 2 for r in daily_returns) / len(daily_returns)
    downside_std = downside_var**0.5
    sortino = (mean_ret / downside_std) * (365**0.5) if downside_std > 0 else 0.0

    volatility = std_ret * (365**0.5) * 100
    return sharpe, sortino, volatility


def _hwm_and_recovery(
    hwm_seed: float | None,
    starting_equity: float,
    current_equity: float,
    twr_equity: list[float],
    equity_curve: list[dict[str, str | float]],
    is_btc: bool,
    first_date: str,
) -> tuple[float, float, float, float, float, float]:
    """Return (hwm_twr_index, twr_recovery_pct, twr_recovery_amount,
    notional_hwm, notional_recovery, notional_recovery_pct)."""
    current_twr_index = twr_equity[-1] if twr_equity else 1.0
    observed_twr_peak = max(twr_equity) if twr_equity else 1.0

    if hwm_seed is not None and hwm_seed > 0 and starting_equity > 0:
        seeded_twr = hwm_seed / starting_equity
        hwm_twr_index = max(seeded_twr, observed_twr_peak)
    else:
        hwm_twr_index = observed_twr_peak

    twr_recovery_pct = 0.0
    twr_recovery_amount = 0.0
    if current_twr_index > 0 and hwm_twr_index > current_twr_index:
        twr_recovery_pct = (hwm_twr_index / current_twr_index - 1.0) * 100
        twr_recovery_amount = current_equity * (hwm_twr_index / current_twr_index - 1.0)

    notional_hwm = hwm_seed if hwm_seed is not None and hwm_seed > 0 else starting_equity
    for i in range(1, len(equity_curve)):
        notional_hwm += _get_transfer(equity_curve[i], equity_curve[i - 1], is_btc, first_date)

    notional_recovery = max(0.0, notional_hwm - current_equity)
    notional_recovery_pct = (
        (notional_recovery / current_equity * 100) if current_equity > 0 else 0.0
    )
    return (
        hwm_twr_index,
        twr_recovery_pct,
        twr_recovery_amount,
        notional_hwm,
        notional_recovery,
        notional_recovery_pct,
    )


def _pnl_recovery_usd_block(
    equity_curve: list[dict[str, str | float]], pnl_recovery: dict[str, float | str]
) -> tuple[float | None, float | None, float | None]:
    """Compute (seed, recovery_usd, recovery_pct) for pnl_based HWM accounts."""
    seed_usd = float(pnl_recovery.get("amount_usd", 0))
    tracking_start = str(pnl_recovery.get("tracking_start", ""))
    pnl_recovery_seed_usd = seed_usd
    if seed_usd <= 0 or not tracking_start:
        return pnl_recovery_seed_usd, None, None

    start_usdt: float | None = None
    end_usdt = 0.0
    usdt_transfers = 0.0
    current_eq_usd = float(equity_curve[-1].get("equity_usd", 0))
    prev_point: dict[str, str | float] | None = None
    for point in equity_curve:
        if str(point.get("date", "")) >= tracking_start:
            usdt_bal = float(point.get("usdt_balance", 0))
            if start_usdt is None:
                start_usdt = usdt_bal
            else:
                prev_usdt = float(prev_point.get("usdt_balance", 0)) if prev_point else usdt_bal
                usdt_change = usdt_bal - prev_usdt
                if point.get("transfer_usd") is not None and abs(usdt_change) > 500:
                    usdt_transfers += usdt_change
            end_usdt = usdt_bal
        prev_point = point

    if start_usdt is None:
        return pnl_recovery_seed_usd, None, None

    usdt_pnl = (end_usdt - start_usdt) - usdt_transfers
    pnl_recovery_usd = max(0.0, seed_usd - usdt_pnl)
    pnl_recovery_usd_pct = pnl_recovery_usd / current_eq_usd * 100 if current_eq_usd > 0 else None
    return pnl_recovery_seed_usd, pnl_recovery_usd, pnl_recovery_usd_pct


def _total_capital_flows(
    equity_curve: list[dict[str, str | float]], is_btc: bool, first_date: str
) -> tuple[float, float]:
    """Return (total_deposits, total_withdrawals) in native units."""
    total_deposits = 0.0
    total_withdrawals = 0.0
    for i in range(1, len(equity_curve)):
        xfer = _get_transfer(equity_curve[i], equity_curve[i - 1], is_btc, first_date)
        if xfer > 0:
            total_deposits += xfer
        elif xfer < 0:
            total_withdrawals += xfer
    return total_deposits, total_withdrawals


def compute_performance_stats(
    equity_curve: list[dict[str, str | float]],
    hwm_seed: float | None = None,
    pnl_recovery: dict[str, float | str] | None = None,
) -> dict[str, float | str]:
    """Compute key performance statistics from equity curve.

    Uses Time-Weighted Return (TWR) to exclude the effect of deposits/withdrawals.
    Each equity curve point may have a `transfer_usd` field indicating net cash flow.

    For BTC-denominated accounts (detected by presence of btc_balance + btc_price_usd),
    all metrics are computed in BTC terms — BTC price appreciation is NOT PNL.
    PNL = native-unit balance growth (share-class agnostic).

    Args:
        equity_curve: Daily equity snapshots.
        hwm_seed: Historical HWM in native units (USD or BTC) from before
            our data starts. If provided, TWR and notional HWMs are seeded
            from this value. If None, HWM starts at inception.
        pnl_recovery: PnL recovery info for pnl_based HWM models (e.g. GP).
            Dict with ``amount_usd`` (USDT to recover) and ``tracking_start``
            (date from which USDT P&L is tracked). Recovery computed in USD
            from USDT balance changes — NOT converted to BTC.
    """
    if len(equity_curve) < 2:
        return {}

    is_btc = _is_btc_account(equity_curve)
    first_date = str(equity_curve[0].get("date", ""))

    equities = [_get_equity(p, is_btc) for p in equity_curve]
    equities = [e for e in equities if e > 0]
    if len(equities) < 2:
        return {}

    daily_returns, flagged_days = _compute_daily_returns(equity_curve, is_btc, first_date)
    twr_product = 1.0
    for dr in daily_returns:
        twr_product *= 1.0 + dr
    total_return_pct = (twr_product - 1.0) * 100

    twr_equity = _twr_equity_series(daily_returns)
    max_dd, max_dd_duration = _max_drawdown_stats(twr_equity)

    current_equity = equities[-1]
    starting_equity = equities[0]
    (
        hwm_twr_index,
        twr_recovery_pct,
        twr_recovery_amount,
        notional_hwm,
        notional_recovery,
        notional_recovery_pct,
    ) = _hwm_and_recovery(
        hwm_seed, starting_equity, current_equity, twr_equity, equity_curve, is_btc, first_date
    )

    pnl_recovery_seed_usd: float | None = None
    pnl_recovery_usd: float | None = None
    pnl_recovery_usd_pct: float | None = None
    if pnl_recovery is not None:
        pnl_recovery_seed_usd, pnl_recovery_usd, pnl_recovery_usd_pct = _pnl_recovery_usd_block(
            equity_curve, pnl_recovery
        )

    sharpe, sortino, volatility = _risk_ratios(daily_returns)

    days = len(equities)
    annualized_return = (
        (twr_product ** (365 / days) - 1.0) * 100 if days > 1 and twr_product > 0 else 0.0
    )
    total_deposits, total_withdrawals = _total_capital_flows(equity_curve, is_btc, first_date)
    calmar = annualized_return / (max_dd * 100) if max_dd > 0 and annualized_return != 0 else 0.0
    simple_return_pct = sum(daily_returns) * 100
    win_days = sum(1 for r in daily_returns if r > 0)
    win_rate = (win_days / len(daily_returns) * 100) if daily_returns else 0

    denomination = "BTC" if is_btc else "USD"
    result: dict[str, float | str] = {
        "denomination": denomination,
        "total_return_pct": round(total_return_pct, 2),  # compounded
        "simple_return_pct": round(simple_return_pct, 2),  # sum of daily
        "annualized_return_pct": round(annualized_return, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration_days": max_dd_duration,
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(calmar, 2),
        "volatility_pct": round(volatility, 2),
        "win_rate_pct": round(win_rate, 1),
        # TWR HWM (Method 1): pure performance, ignores capital flows
        "high_water_mark_twr": round(hwm_twr_index, 6),
        "twr_recovery_pct": round(twr_recovery_pct, 2),
        "twr_recovery_amount": round(twr_recovery_amount, 6 if is_btc else 2),
        # Notional HWM (Method 2): transfer-adjusted, tracks actual $ needed
        "notional_hwm": round(notional_hwm, 6 if is_btc else 2),
        "notional_recovery": round(notional_recovery, 6 if is_btc else 2),
        "notional_recovery_pct": round(notional_recovery_pct, 2),
        "equity_curve_days": days,
        "start_date": str(equity_curve[0].get("date", "")),
        "end_date": str(equity_curve[-1].get("date", "")),
    }
    xfer_decimals = 6 if is_btc else 2
    if total_deposits > 0:
        result["total_deposits"] = round(total_deposits, xfer_decimals)
    if total_withdrawals < 0:
        result["total_withdrawals"] = round(total_withdrawals, xfer_decimals)
    if flagged_days:
        result["flagged_days"] = flagged_days
    if pnl_recovery_seed_usd is not None:
        result["pnl_recovery_seed_usd"] = pnl_recovery_seed_usd
    if pnl_recovery_usd is not None:
        result["pnl_recovery_usd"] = round(pnl_recovery_usd, 2)
    if pnl_recovery_usd_pct is not None:
        result["pnl_recovery_usd_pct"] = round(pnl_recovery_usd_pct, 2)

    return result
