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
class CoinBreakdown:  # CORRECT-LOCAL: service-internal trade analytics dataclass
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
class TradeAnalytics:  # CORRECT-LOCAL: service-internal trade analytics dataclass
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


def _load_json(client_id: str, filename: str) -> list[dict[str, str | float | None]] | None:
    path = _DATA_DIR / client_id / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


@dataclass
class _BillsAggregate:
    """Per-coin sums extracted from a bills_ledger.json file."""

    realized: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    fees: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    funding: dict[str, float] = field(default_factory=lambda: defaultdict(float))


@dataclass
class _TradesAggregate:
    """Per-coin and per-day sums extracted from a trades.json file."""

    volume: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    buys: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    sells: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    daily_volume: dict[str, float] = field(default_factory=lambda: defaultdict(float))


def _aggregate_bills(bills: list[dict[str, str | float | None]]) -> _BillsAggregate:
    """Sum realized PnL, fees, and funding per coin from a bills ledger."""
    agg = _BillsAggregate()
    for b in bills:
        inst_raw = b.get("inst_id", "")
        inst = str(inst_raw) if inst_raw else ""
        sym = inst.split("-")[0] if inst else ""
        if not sym:
            continue
        change = float(b.get("change", 0))
        sub_type = b.get("sub_type", "")
        bill_type = b.get("type", "")
        if sub_type == "5":  # realized PnL from closing
            agg.realized[sym] += change
        elif sub_type == "3":  # trading fee (negative = cost)
            agg.fees[sym] += change
        elif sub_type in ("173", "174") or bill_type == "8":  # funding
            agg.funding[sym] += change
    return agg


def _aggregate_trades(
    trades: list[dict[str, str | float | None]],
) -> _TradesAggregate:
    """Sum volume, trade counts, and daily volume per coin from trade events."""
    agg = _TradesAggregate()
    for t in trades:
        raw_sym = t.get("symbol", "")
        sym = str(raw_sym).split("/")[0] if raw_sym is not None else ""
        vol = float(t.get("cost", 0) or 0)
        agg.volume[sym] += vol
        agg.counts[sym] += 1
        if t.get("side") == "buy":
            agg.buys[sym] += 1
        else:
            agg.sells[sym] += 1
        dt_raw = t.get("datetime", "")
        dt_str = str(dt_raw) if dt_raw is not None else ""
        if dt_str:
            agg.daily_volume[dt_str[:10]] += vol
    return agg


def _build_coin_breakdowns(
    bills_agg: _BillsAggregate,
    trades_agg: _TradesAggregate,
    coin_holding: dict[str, dict[str, float | int]],
) -> list[CoinBreakdown]:
    """Combine bill, trade, and holding aggregates into per-coin breakdowns."""
    all_coins = sorted(set(bills_agg.realized.keys()) | set(trades_agg.volume.keys()))
    coins: list[CoinBreakdown] = []
    for sym in all_coins:
        realized = bills_agg.realized.get(sym, 0.0)
        fees = bills_agg.fees.get(sym, 0.0)
        funding = bills_agg.funding.get(sym, 0.0)
        vol = trades_agg.volume.get(sym, 0.0)
        tc = trades_agg.counts.get(sym, 0)
        holding = coin_holding.get(sym, {})
        coins.append(
            CoinBreakdown(
                symbol=sym,
                realized_pnl=round(realized, 2),
                trading_fees=round(fees, 2),
                funding_pnl=round(funding, 2),
                net_pnl=round(realized + fees + funding, 2),
                volume_usd=round(vol, 2),
                trade_count=tc,
                buy_count=trades_agg.buys.get(sym, 0),
                sell_count=trades_agg.sells.get(sym, 0),
                avg_trade_size_usd=round(vol / tc, 2) if tc > 0 else 0,
                avg_holding_hours=round(float(holding.get("avg_hours", 0)), 1),
                total_holding_hours=round(float(holding.get("total_hours", 0)), 1),
                round_trips=int(holding.get("round_trips", 0)),
            )
        )
    coins.sort(key=lambda c: abs(c.net_pnl), reverse=True)
    return coins


def _populate_totals(
    analytics: TradeAnalytics,
    bills_agg: _BillsAggregate,
    trades_agg: _TradesAggregate,
) -> None:
    """Set scalar totals on ``analytics`` from the bill/trade aggregates."""
    analytics.total_realized_pnl = round(sum(bills_agg.realized.values()), 2)
    analytics.total_trading_fees = round(sum(bills_agg.fees.values()), 2)
    analytics.total_funding_pnl = round(sum(bills_agg.funding.values()), 2)
    analytics.total_net_pnl = round(
        analytics.total_realized_pnl + analytics.total_trading_fees + analytics.total_funding_pnl,
        2,
    )
    analytics.total_volume_usd = round(sum(trades_agg.volume.values()), 2)
    analytics.total_trade_count = sum(trades_agg.counts.values())


def _populate_daily_series(analytics: TradeAnalytics, daily_vol: dict[str, float]) -> None:
    """Set the daily volume series and derived calendar fields."""
    sorted_days = sorted(daily_vol.keys())
    analytics.daily_volumes = [{"date": d, "volume_usd": round(daily_vol[d], 2)} for d in sorted_days]
    analytics.trading_days = len(sorted_days)
    if sorted_days:
        analytics.first_trade_date = sorted_days[0]
        analytics.last_trade_date = sorted_days[-1]
        analytics.avg_daily_volume_usd = round(analytics.total_volume_usd / len(sorted_days), 2)


def _populate_capital_deployment(analytics: TradeAnalytics, client_id: str) -> None:
    """Compute capital deployment ratio = avg daily volume / avg equity."""
    equity_curve = _load_json(client_id, "equity_curve.json") or []
    if not equity_curve or analytics.avg_daily_volume_usd <= 0:
        return
    avg_equity = sum(float(p.get("equity_usd", 0)) for p in equity_curve) / len(equity_curve)
    if avg_equity > 0:
        analytics.capital_deployment_ratio = round(analytics.avg_daily_volume_usd / avg_equity, 4)


def _populate_holding_aggregates(
    analytics: TradeAnalytics,
    coin_holding: dict[str, dict[str, float | int]],
) -> None:
    """Set portfolio-level holding stats by weighting per-coin averages."""
    all_hours: list[float] = []
    total_rts = 0
    for h in coin_holding.values():
        round_trips = int(h.get("round_trips", 0))
        if round_trips > 0:
            all_hours.extend([float(h["avg_hours"])] * round_trips)
            total_rts += round_trips
    analytics.total_round_trips = total_rts
    if all_hours:
        analytics.avg_holding_hours = round(sum(all_hours) / len(all_hours), 1)


def _compute_monthly_pnl(bills: list[dict[str, str | float | None]]) -> list[dict[str, str | float]]:
    """Bucket bill ledger entries into a YYYY-MM PnL series."""
    monthly_pnl: dict[str, float] = defaultdict(float)
    for b in bills:
        inst_raw = b.get("inst_id", "")
        if not inst_raw:
            continue
        ts = b.get("timestamp", 0)
        if not ts:
            continue
        dt = datetime.fromtimestamp(float(ts) / 1000, tz=UTC)
        month = dt.strftime("%Y-%m")
        monthly_pnl[month] += float(b.get("change", 0))
    sorted_months = sorted(monthly_pnl.keys())
    return [{"month": m, "pnl_usd": round(monthly_pnl[m], 2)} for m in sorted_months]


def compute_coin_breakdown(client_id: str) -> TradeAnalytics:
    """Compute full coin-level PnL + volume breakdown for a client.

    Uses bills_ledger.json for PnL (complete history) and trades.json for volume/count.
    """
    analytics = TradeAnalytics(client_id=client_id)

    bills = _load_json(client_id, "bills_ledger.json")
    if not bills:
        return analytics

    bills_agg = _aggregate_bills(bills)
    trades = _load_json(client_id, "trades.json") or []
    trades_agg = _aggregate_trades(trades)
    coin_holding = _compute_holding_times(trades)

    analytics.coins = _build_coin_breakdowns(bills_agg, trades_agg, coin_holding)
    _populate_totals(analytics, bills_agg, trades_agg)
    _populate_daily_series(analytics, trades_agg.daily_volume)
    _populate_capital_deployment(analytics, client_id)
    _populate_holding_aggregates(analytics, coin_holding)
    analytics.monthly_pnl = _compute_monthly_pnl(bills)

    return analytics


def _group_trades_by_coin(
    trades: list[dict[str, str | float | None]],
) -> dict[str, list[dict[str, str | float | None]]]:
    """Group trade events by base coin symbol (e.g. ``BTC/USDT`` → ``BTC``)."""
    coin_trades: dict[str, list[dict[str, str | float | None]]] = defaultdict(list)
    for t in trades:
        raw_symbol = t.get("symbol", "")
        symbol_str = str(raw_symbol) if raw_symbol is not None else ""
        sym = symbol_str.split("/")[0]
        coin_trades[sym].append(t)
    return coin_trades


def _match_sell_against_buy_queue(
    sell_ts: float,
    sell_amount: float,
    buy_queue: list[tuple[float, float]],
) -> list[float]:
    """FIFO-match a single sell against the buy queue, returning hold durations.

    Mutates ``buy_queue`` in place: filled buys are popped; partial fills are
    rewritten with the remaining size.
    """
    durations: list[float] = []
    remaining = sell_amount
    while remaining > 0 and buy_queue:
        buy_ts, buy_amt = buy_queue[0]
        matched = min(remaining, buy_amt)
        duration_hours = (sell_ts - buy_ts) / (1000 * 3600)
        if duration_hours > 0:
            durations.append(duration_hours)
        remaining -= matched
        if matched >= buy_amt:
            buy_queue.pop(0)
        else:
            buy_queue[0] = (buy_ts, buy_amt - matched)
    return durations


def _holding_durations_for_coin(
    sym_trades: list[dict[str, str | float | None]],
) -> list[float]:
    """Compute the list of round-trip hold durations (hours) for one coin."""
    sym_trades.sort(key=lambda x: x.get("timestamp", 0) or 0)
    buy_queue: list[tuple[float, float]] = []  # (timestamp, amount)
    durations: list[float] = []
    for t in sym_trades:
        ts = float(t.get("timestamp", 0) or 0)
        amt = float(t.get("amount", 0) or 0)
        side = t.get("side", "")
        if side == "buy":
            buy_queue.append((ts, amt))
        elif side == "sell" and buy_queue:
            durations.extend(_match_sell_against_buy_queue(ts, amt, buy_queue))
    return durations


def _summarise_holding_durations(durations: list[float]) -> dict[str, float | int]:
    """Reduce a list of hold durations to summary stats."""
    return {
        "avg_hours": sum(durations) / len(durations),
        "total_hours": sum(durations),
        "round_trips": len(durations),
    }


def _compute_holding_times(
    trades: list[dict[str, str | float | None]],
) -> dict[str, dict[str, float | int]]:
    """Compute average holding time per coin using FIFO matching of buys/sells.

    Returns dict[symbol] -> {avg_hours, total_hours, round_trips}
    """
    coin_trades = _group_trades_by_coin(trades)
    result: dict[str, dict[str, float | int]] = {}
    for sym, sym_trades in coin_trades.items():
        durations = _holding_durations_for_coin(sym_trades)
        if durations:
            result[sym] = _summarise_holding_durations(durations)
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


def _load_per_client_analytics(client_ids: list[str]) -> list[TradeAnalytics]:
    """Load per-client analytics, skipping clients without backfill data."""
    return [compute_coin_breakdown(cid) for cid in client_ids if (_DATA_DIR / cid).exists()]


def _accumulate_coin_into(target: CoinBreakdown, source: CoinBreakdown) -> None:
    """Add ``source`` coin metrics into ``target`` (in place)."""
    target.realized_pnl += source.realized_pnl
    target.trading_fees += source.trading_fees
    target.funding_pnl += source.funding_pnl
    target.net_pnl += source.net_pnl
    target.volume_usd += source.volume_usd
    target.trade_count += source.trade_count
    target.buy_count += source.buy_count
    target.sell_count += source.sell_count
    target.round_trips += source.round_trips
    target.total_holding_hours += source.total_holding_hours


def _accumulate_totals_into(target: TradeAnalytics, source: TradeAnalytics) -> None:
    """Add scalar totals from ``source`` into ``target`` (in place)."""
    target.total_realized_pnl += source.total_realized_pnl
    target.total_trading_fees += source.total_trading_fees
    target.total_funding_pnl += source.total_funding_pnl
    target.total_net_pnl += source.total_net_pnl
    target.total_volume_usd += source.total_volume_usd
    target.total_trade_count += source.total_trade_count
    target.total_round_trips += source.total_round_trips


def _merge_per_client(
    aggregated: TradeAnalytics,
    per_client: list[TradeAnalytics],
) -> tuple[dict[str, CoinBreakdown], dict[str, float], dict[str, float]]:
    """Merge per-client analytics into ``aggregated`` totals + return per-axis dicts."""
    coin_agg: dict[str, CoinBreakdown] = {}
    daily_vol_agg: dict[str, float] = defaultdict(float)
    monthly_pnl_agg: dict[str, float] = defaultdict(float)

    for a in per_client:
        for c in a.coins:
            if c.symbol not in coin_agg:
                coin_agg[c.symbol] = CoinBreakdown(symbol=c.symbol)
            _accumulate_coin_into(coin_agg[c.symbol], c)
        for dv in a.daily_volumes:
            daily_vol_agg[str(dv["date"])] += float(dv["volume_usd"])
        for mp in a.monthly_pnl:
            monthly_pnl_agg[str(mp["month"])] += float(mp["pnl_usd"])
        _accumulate_totals_into(aggregated, a)

    return coin_agg, daily_vol_agg, monthly_pnl_agg


def _finalise_aggregated_coin(coin: CoinBreakdown) -> None:
    """Compute averages and round all monetary fields for an aggregated coin."""
    if coin.trade_count > 0:
        coin.avg_trade_size_usd = round(coin.volume_usd / coin.trade_count, 2)
    if coin.round_trips > 0:
        coin.avg_holding_hours = round(coin.total_holding_hours / coin.round_trips, 1)
    coin.realized_pnl = round(coin.realized_pnl, 2)
    coin.trading_fees = round(coin.trading_fees, 2)
    coin.funding_pnl = round(coin.funding_pnl, 2)
    coin.net_pnl = round(coin.net_pnl, 2)
    coin.volume_usd = round(coin.volume_usd, 2)


def aggregate_clients(client_ids: list[str]) -> TradeAnalytics:
    """Aggregate analytics across multiple clients."""
    aggregated = TradeAnalytics(client_id="AGGREGATE")
    per_client = _load_per_client_analytics(client_ids)
    coin_agg, daily_vol_agg, monthly_pnl_agg = _merge_per_client(aggregated, per_client)

    for coin in coin_agg.values():
        _finalise_aggregated_coin(coin)

    aggregated.coins = sorted(coin_agg.values(), key=lambda c: abs(c.net_pnl), reverse=True)
    aggregated.daily_volumes = [{"date": d, "volume_usd": round(v, 2)} for d, v in sorted(daily_vol_agg.items())]
    aggregated.monthly_pnl = [{"month": m, "pnl_usd": round(v, 2)} for m, v in sorted(monthly_pnl_agg.items())]
    aggregated.trading_days = len(daily_vol_agg)
    if aggregated.trading_days > 0:
        aggregated.avg_daily_volume_usd = round(aggregated.total_volume_usd / aggregated.trading_days, 2)

    return aggregated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _logger = logging.getLogger(__name__)
    for a in compute_all_clients():
        _logger.info("%s", "=" * 60)
        _logger.info("%s - %s", a.client_id, a.strategy)
        _logger.info("  Volume: $%s | Trades: %s", f"{a.total_volume_usd:,.0f}", a.total_trade_count)
        _logger.info("  Realized PnL: $%s", f"{a.total_realized_pnl:,.2f}")
        _logger.info("  Fees: $%s", f"{a.total_trading_fees:,.2f}")
        _logger.info("  Funding: $%s", f"{a.total_funding_pnl:,.2f}")
        _logger.info("  Net PnL: $%s", f"{a.total_net_pnl:,.2f}")
        _logger.info("  Avg daily vol: $%s", f"{a.avg_daily_volume_usd:,.0f}")
        _logger.info("  Capital deployment: %s", f"{a.capital_deployment_ratio:.2%}")
        _logger.info(
            "  Avg holding: %sh | Round trips: %s",
            f"{a.avg_holding_hours:.1f}",
            a.total_round_trips,
        )
        _logger.info("  Top coins:")
        for c in a.coins[:5]:
            _logger.info(
                "    %s: $%s PnL | $%s vol | %s trades | %sh hold",
                f"{c.symbol:>6}",
                f"{c.net_pnl:>10,.2f}",
                f"{c.volume_usd:>10,.0f}",
                c.trade_count,
                f"{c.avg_holding_hours:.1f}",
            )
