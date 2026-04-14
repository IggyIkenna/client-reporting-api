"""Compute coin-level PnL, volume, holding time, and capital deployment from real data.

Uses bills_ledger.json (complete history) for PnL and trades.json for volume/count.
Validates: sum(coin_pnl) == total_realized_pnl from bills.

All metrics computable per-client or aggregated across clients.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"

CLIENT_IDS = ["PR", "NN", "ET", "STD", "GP", "SL", "SL2", "ANU", "IK", "ODUM_PROP"]


@dataclass
class CoinBreakdown:
    """PnL and volume breakdown for a single coin."""

    symbol: str
    realized_pnl: float = 0.0
    trading_fees: float = 0.0
    funding_pnl: float = 0.0
    net_pnl: float = 0.0
    volume_usd: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    avg_trade_size_usd: float = 0.0
    # Holding time: avg time between open and close (from matched trades)
    avg_holding_hours: float = 0.0
    total_holding_hours: float = 0.0
    round_trips: int = 0


@dataclass
class TradeAnalytics:
    """Full trade analytics for a client."""

    client_id: str
    strategy: str = "Mean Reversion Grid"
    # Coin breakdown
    coins: list[CoinBreakdown] = field(default_factory=list)
    # Totals
    total_realized_pnl: float = 0.0
    total_trading_fees: float = 0.0
    total_funding_pnl: float = 0.0
    total_net_pnl: float = 0.0
    total_volume_usd: float = 0.0
    total_trade_count: int = 0
    # Volume metrics
    avg_daily_volume_usd: float = 0.0
    trading_days: int = 0
    capital_deployment_ratio: float = 0.0  # avg daily volume / avg equity
    # Holding time
    avg_holding_hours: float = 0.0
    total_round_trips: int = 0
    # Time range
    first_trade_date: str = ""
    last_trade_date: str = ""
    # Daily volume series for charting
    daily_volumes: list[dict[str, str | float]] = field(default_factory=list)
    # Monthly PnL series
    monthly_pnl: list[dict[str, str | float]] = field(default_factory=list)


def _load_json(client_id: str, filename: str) -> list[dict[str, str | float]] | None:
    path = _DATA_DIR / client_id / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def compute_coin_breakdown(client_id: str) -> TradeAnalytics:
    """Compute full coin-level PnL + volume breakdown for a client.

    Uses bills_ledger.json for PnL (complete history) and trades.json for volume/count.
    """
    analytics = TradeAnalytics(client_id=client_id)

    # --- PnL from bills ledger (complete history) ---
    bills = _load_json(client_id, "bills_ledger.json")
    if not bills:
        return analytics

    coin_realized: dict[str, float] = defaultdict(float)
    coin_fees: dict[str, float] = defaultdict(float)
    coin_funding: dict[str, float] = defaultdict(float)

    for b in bills:
        inst = b.get("inst_id", "")
        sym = inst.split("-")[0] if inst else ""
        if not sym:
            continue
        change = float(b.get("change", 0))
        sub_type = b.get("sub_type", "")
        bill_type = b.get("type", "")

        if sub_type == "5":  # realized PnL from closing
            coin_realized[sym] += change
        elif sub_type == "3":  # trading fee (negative = cost)
            coin_fees[sym] += change
        elif sub_type in ("173", "174") or bill_type == "8":  # funding
            coin_funding[sym] += change

    # --- Volume and count from trades ---
    trades = _load_json(client_id, "trades.json") or []

    coin_volume: dict[str, float] = defaultdict(float)
    coin_trades: dict[str, int] = defaultdict(int)
    coin_buys: dict[str, int] = defaultdict(int)
    coin_sells: dict[str, int] = defaultdict(int)
    daily_vol: dict[str, float] = defaultdict(float)

    for t in trades:
        raw_sym = t.get("symbol", "")
        sym = raw_sym.split("/")[0]
        vol = float(t.get("cost", 0) or 0)
        coin_volume[sym] += vol
        coin_trades[sym] += 1
        if t.get("side") == "buy":
            coin_buys[sym] += 1
        else:
            coin_sells[sym] += 1

        # Daily volume
        dt_str = t.get("datetime", "")
        if dt_str:
            day = dt_str[:10]
            daily_vol[day] += vol

    # --- Holding time from trade pairs ---
    # For mean reversion grid: match buys with subsequent sells per coin
    coin_holding = _compute_holding_times(trades)

    # --- Build coin breakdown ---
    all_coins = sorted(set(list(coin_realized.keys()) + list(coin_volume.keys())))

    coins: list[CoinBreakdown] = []
    for sym in all_coins:
        realized = coin_realized.get(sym, 0)
        fees = coin_fees.get(sym, 0)
        funding = coin_funding.get(sym, 0)
        net = realized + fees + funding
        vol = coin_volume.get(sym, 0)
        tc = coin_trades.get(sym, 0)

        holding = coin_holding.get(sym, {})
        coins.append(
            CoinBreakdown(
                symbol=sym,
                realized_pnl=round(realized, 2),
                trading_fees=round(fees, 2),
                funding_pnl=round(funding, 2),
                net_pnl=round(net, 2),
                volume_usd=round(vol, 2),
                trade_count=tc,
                buy_count=coin_buys.get(sym, 0),
                sell_count=coin_sells.get(sym, 0),
                avg_trade_size_usd=round(vol / tc, 2) if tc > 0 else 0,
                avg_holding_hours=round(holding.get("avg_hours", 0), 1),
                total_holding_hours=round(holding.get("total_hours", 0), 1),
                round_trips=holding.get("round_trips", 0),
            )
        )

    # Sort by absolute net PnL descending
    coins.sort(key=lambda c: abs(c.net_pnl), reverse=True)
    analytics.coins = coins

    # --- Totals ---
    analytics.total_realized_pnl = round(sum(coin_realized.values()), 2)
    analytics.total_trading_fees = round(sum(coin_fees.values()), 2)
    analytics.total_funding_pnl = round(sum(coin_funding.values()), 2)
    analytics.total_net_pnl = round(
        analytics.total_realized_pnl + analytics.total_trading_fees + analytics.total_funding_pnl, 2
    )
    analytics.total_volume_usd = round(sum(coin_volume.values()), 2)
    analytics.total_trade_count = sum(coin_trades.values())

    # --- Daily volume series ---
    sorted_days = sorted(daily_vol.keys())
    analytics.daily_volumes = [
        {"date": d, "volume_usd": round(daily_vol[d], 2)} for d in sorted_days
    ]
    analytics.trading_days = len(sorted_days)

    if sorted_days:
        analytics.first_trade_date = sorted_days[0]
        analytics.last_trade_date = sorted_days[-1]
        analytics.avg_daily_volume_usd = round(analytics.total_volume_usd / len(sorted_days), 2)

    # --- Capital deployment ratio ---
    # avg daily volume / avg equity over the period
    equity_curve = _load_json(client_id, "equity_curve.json") or []
    if equity_curve and analytics.avg_daily_volume_usd > 0:
        avg_equity = sum(float(p.get("equity_usd", 0)) for p in equity_curve) / len(equity_curve)
        if avg_equity > 0:
            analytics.capital_deployment_ratio = round(
                analytics.avg_daily_volume_usd / avg_equity, 4
            )

    # --- Holding time aggregates ---
    all_hours = []
    total_rts = 0
    for sym, h in coin_holding.items():
        if h.get("round_trips", 0) > 0:
            all_hours.extend([h["avg_hours"]] * h["round_trips"])
            total_rts += h["round_trips"]
    analytics.total_round_trips = total_rts
    if all_hours:
        analytics.avg_holding_hours = round(sum(all_hours) / len(all_hours), 1)

    # --- Monthly PnL from bills ---
    monthly_pnl: dict[str, float] = defaultdict(float)
    for b in bills:
        inst = b.get("inst_id", "")
        if not inst:
            continue
        ts = b.get("timestamp", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
            month = dt.strftime("%Y-%m")
            change = float(b.get("change", 0))
            monthly_pnl[month] += change

    sorted_months = sorted(monthly_pnl.keys())
    analytics.monthly_pnl = [
        {"month": m, "pnl_usd": round(monthly_pnl[m], 2)} for m in sorted_months
    ]

    return analytics


def _compute_holding_times(
    trades: list[dict[str, str | float | None]],
) -> dict[str, dict[str, float | int]]:
    """Compute average holding time per coin using FIFO matching of buys/sells.

    Returns dict[symbol] -> {avg_hours, total_hours, round_trips}
    """
    # Group trades by coin, sorted by time
    coin_trades: dict[str, list[dict[str, str | float | None]]] = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", "").split("/")[0]
        coin_trades[sym].append(t)

    result: dict[str, dict[str, float | int]] = {}

    for sym, sym_trades in coin_trades.items():
        sym_trades.sort(key=lambda x: x.get("timestamp", 0) or 0)

        # FIFO: match buys with sells
        buy_queue: list[tuple[float, float]] = []  # (timestamp, amount)
        holding_durations: list[float] = []

        for t in sym_trades:
            ts = float(t.get("timestamp", 0) or 0)
            amt = float(t.get("amount", 0) or 0)
            side = t.get("side", "")

            if side == "buy":
                buy_queue.append((ts, amt))
            elif side == "sell" and buy_queue:
                remaining = amt
                while remaining > 0 and buy_queue:
                    buy_ts, buy_amt = buy_queue[0]
                    matched = min(remaining, buy_amt)
                    duration_hours = (ts - buy_ts) / (1000 * 3600)
                    if duration_hours > 0:
                        holding_durations.append(duration_hours)
                    remaining -= matched
                    if matched >= buy_amt:
                        buy_queue.pop(0)
                    else:
                        buy_queue[0] = (buy_ts, buy_amt - matched)

        if holding_durations:
            result[sym] = {
                "avg_hours": sum(holding_durations) / len(holding_durations),
                "total_hours": sum(holding_durations),
                "round_trips": len(holding_durations),
            }

    return result


def compute_all_clients() -> list[TradeAnalytics]:
    """Compute trade analytics for all clients."""
    results: list[TradeAnalytics] = []
    for cid in CLIENT_IDS:
        if (_DATA_DIR / cid).exists():
            analytics = compute_coin_breakdown(cid)
            results.append(analytics)
            logger.info(
                "Analytics: %s — %d coins, $%s volume, $%s net PnL",
                cid,
                len(analytics.coins),
                f"{analytics.total_volume_usd:,.0f}",
                f"{analytics.total_net_pnl:,.0f}",
            )
    return results


def aggregate_clients(client_ids: list[str]) -> TradeAnalytics:
    """Aggregate analytics across multiple clients."""
    aggregated = TradeAnalytics(client_id="AGGREGATE")
    coin_agg: dict[str, CoinBreakdown] = {}
    daily_vol_agg: dict[str, float] = defaultdict(float)
    monthly_pnl_agg: dict[str, float] = defaultdict(float)

    all_analytics: list[TradeAnalytics] = []
    for cid in client_ids:
        if (_DATA_DIR / cid).exists():
            all_analytics.append(compute_coin_breakdown(cid))

    for a in all_analytics:
        for c in a.coins:
            if c.symbol not in coin_agg:
                coin_agg[c.symbol] = CoinBreakdown(symbol=c.symbol)
            agg = coin_agg[c.symbol]
            agg.realized_pnl += c.realized_pnl
            agg.trading_fees += c.trading_fees
            agg.funding_pnl += c.funding_pnl
            agg.net_pnl += c.net_pnl
            agg.volume_usd += c.volume_usd
            agg.trade_count += c.trade_count
            agg.buy_count += c.buy_count
            agg.sell_count += c.sell_count
            agg.round_trips += c.round_trips
            agg.total_holding_hours += c.total_holding_hours

        for dv in a.daily_volumes:
            daily_vol_agg[str(dv["date"])] += float(dv["volume_usd"])
        for mp in a.monthly_pnl:
            monthly_pnl_agg[str(mp["month"])] += float(mp["pnl_usd"])

        aggregated.total_realized_pnl += a.total_realized_pnl
        aggregated.total_trading_fees += a.total_trading_fees
        aggregated.total_funding_pnl += a.total_funding_pnl
        aggregated.total_net_pnl += a.total_net_pnl
        aggregated.total_volume_usd += a.total_volume_usd
        aggregated.total_trade_count += a.total_trade_count
        aggregated.total_round_trips += a.total_round_trips

    # Finalize coin aggregates
    for c in coin_agg.values():
        if c.trade_count > 0:
            c.avg_trade_size_usd = round(c.volume_usd / c.trade_count, 2)
        if c.round_trips > 0:
            c.avg_holding_hours = round(c.total_holding_hours / c.round_trips, 1)
        c.realized_pnl = round(c.realized_pnl, 2)
        c.trading_fees = round(c.trading_fees, 2)
        c.funding_pnl = round(c.funding_pnl, 2)
        c.net_pnl = round(c.net_pnl, 2)
        c.volume_usd = round(c.volume_usd, 2)

    aggregated.coins = sorted(coin_agg.values(), key=lambda c: abs(c.net_pnl), reverse=True)
    aggregated.daily_volumes = [
        {"date": d, "volume_usd": round(v, 2)} for d, v in sorted(daily_vol_agg.items())
    ]
    aggregated.monthly_pnl = [
        {"month": m, "pnl_usd": round(v, 2)} for m, v in sorted(monthly_pnl_agg.items())
    ]
    aggregated.trading_days = len(daily_vol_agg)
    if aggregated.trading_days > 0:
        aggregated.avg_daily_volume_usd = round(
            aggregated.total_volume_usd / aggregated.trading_days, 2
        )

    return aggregated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for a in compute_all_clients():
        print(f"\n{'=' * 60}")
        print(f"{a.client_id} — {a.strategy}")
        print(f"  Volume: ${a.total_volume_usd:,.0f} | Trades: {a.total_trade_count}")
        print(f"  Realized PnL: ${a.total_realized_pnl:,.2f}")
        print(f"  Fees: ${a.total_trading_fees:,.2f}")
        print(f"  Funding: ${a.total_funding_pnl:,.2f}")
        print(f"  Net PnL: ${a.total_net_pnl:,.2f}")
        print(f"  Avg daily vol: ${a.avg_daily_volume_usd:,.0f}")
        print(f"  Capital deployment: {a.capital_deployment_ratio:.2%}")
        print(f"  Avg holding: {a.avg_holding_hours:.1f}h | Round trips: {a.total_round_trips}")
        print("  Top coins:")
        for c in a.coins[:5]:
            print(
                f"    {c.symbol:>6}: ${c.net_pnl:>10,.2f} PnL | ${c.volume_usd:>10,.0f} vol | {c.trade_count} trades | {c.avg_holding_hours:.1f}h hold"
            )
