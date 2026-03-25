"""Sports PnL report generation from GCS Parquet data via UCI DataSource.

Reads from: pnl/sports/{period_month}/{strategy_or_all}/sports_pnl.parquet
Bucket: PROTOCOL_DATA_SOURCE_BUCKET_PNL (or PROTOCOL_DATA_SOURCE_BUCKET fallback)

Architecture:
  pnl-attribution-service (writer) → GCS bucket → client-reporting-api (reader)
  SportsPnLEngine.persist_to_gcs() → upload_to_storage() → pnl/sports/{date}/{strategy}/
  sports_pnl_reader.py → get_data_source("sports_pnl") → read Parquet → API response
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from unified_trading_library import get_data_source

logger = logging.getLogger(__name__)

_SPORTS_PNL_ROUTING_KEY = "sports_pnl"
_PNL_ROUTING_KEY = "pnl"


def _read_sports_dataframe(prefix: str) -> pd.DataFrame:
    """Read sports PnL Parquet from GCS. Falls back to main pnl routing key."""
    for routing_key in (_SPORTS_PNL_ROUTING_KEY, _PNL_ROUTING_KEY):
        try:
            source = get_data_source(routing_key=routing_key, prefix=prefix)
            result = source.read(format="parquet")
            if isinstance(result, pd.DataFrame) and not result.empty:
                return result
        except (FileNotFoundError, KeyError, ValueError):
            continue
    return pd.DataFrame()


def generate_sports_pnl_report(client_id: str, period_month: str) -> dict[str, object]:
    """Sports P&L breakdown by venue and strategy from GCS Parquet data."""
    prefix = f"pnl/sports/{period_month}/{client_id}/"
    logger.info(
        "Generating sports PnL: client=%s period=%s prefix=%s", client_id, period_month, prefix
    )

    df = _read_sports_dataframe(prefix)
    if df.empty:
        return {"status": "no_data", "client_id": client_id, "period_month": period_month}

    total_pnl = (
        Decimal(str(df["profit_loss"].sum())) if "profit_loss" in df.columns else Decimal("0")
    )
    total_stake = Decimal(str(df["stake"].sum())) if "stake" in df.columns else Decimal("0")
    roi_pct = (total_pnl / total_stake * 100) if total_stake > 0 else Decimal("0")

    by_venue: list[dict[str, object]] = []
    if "venue_key" in df.columns:
        for venue, grp in df.groupby("venue_key"):
            v_pnl = (
                Decimal(str(grp["profit_loss"].sum()))
                if "profit_loss" in grp.columns
                else Decimal("0")
            )
            v_stake = Decimal(str(grp["stake"].sum())) if "stake" in grp.columns else Decimal("0")
            by_venue.append(
                {
                    "venue": str(venue),
                    "pnl": str(v_pnl),
                    "volume": str(v_stake),
                    "bets": len(grp),
                    "roi_pct": str((v_pnl / v_stake * 100) if v_stake > 0 else Decimal("0")),
                }
            )

    by_strategy: list[dict[str, object]] = []
    if "strategy_id" in df.columns:
        for strat, grp in df.groupby("strategy_id"):
            s_pnl = (
                Decimal(str(grp["profit_loss"].sum()))
                if "profit_loss" in grp.columns
                else Decimal("0")
            )
            by_strategy.append(
                {
                    "strategy": str(strat),
                    "pnl": str(s_pnl),
                    "bets": len(grp),
                }
            )

    return {
        "status": "ok",
        "client_id": client_id,
        "period_month": period_month,
        "total_pnl": str(total_pnl),
        "total_volume": str(total_stake),
        "total_bets": len(df),
        "roi_pct": str(roi_pct),
        "by_venue": by_venue,
        "by_strategy": by_strategy,
    }


def generate_clv_report(client_id: str, period_month: str) -> dict[str, object]:
    """CLV analysis from persisted sports PnL data."""
    prefix = f"pnl/sports/{period_month}/{client_id}/"
    df = _read_sports_dataframe(prefix)
    if df.empty:
        return {"status": "no_data", "client_id": client_id, "period_month": period_month}

    with_clv = df[df["clv_edge_pct"].notna()] if "clv_edge_pct" in df.columns else pd.DataFrame()
    if with_clv.empty:
        return {"status": "no_clv_data", "client_id": client_id, "period_month": period_month}

    avg_clv = Decimal(str(with_clv["clv_edge_pct"].mean()))
    positive_count = int((with_clv["clv_edge_pct"] > 0).sum())

    by_venue: list[dict[str, object]] = []
    if "venue_key" in with_clv.columns:
        for venue, grp in with_clv.groupby("venue_key"):
            by_venue.append(
                {
                    "venue": str(venue),
                    "avg_clv": str(Decimal(str(grp["clv_edge_pct"].mean()))),
                    "bets": len(grp),
                }
            )

    return {
        "status": "ok",
        "client_id": client_id,
        "period_month": period_month,
        "average_clv_edge_pct": str(avg_clv),
        "total_bets_with_clv": len(with_clv),
        "positive_clv_pct": str(Decimal(str(positive_count)) / Decimal(str(len(with_clv))) * 100),
        "by_venue": by_venue,
    }


def generate_venue_performance_report(client_id: str) -> dict[str, object]:
    """Per-venue performance from latest sports PnL data."""
    prefix = f"pnl/sports/latest/{client_id}/"
    df = _read_sports_dataframe(prefix)
    if df.empty:
        return {"status": "no_data", "client_id": client_id}

    venues: list[dict[str, object]] = []
    if "venue_key" in df.columns:
        for venue, grp in df.groupby("venue_key"):
            v_pnl = (
                Decimal(str(grp["profit_loss"].sum()))
                if "profit_loss" in grp.columns
                else Decimal("0")
            )
            v_stake = Decimal(str(grp["stake"].sum())) if "stake" in grp.columns else Decimal("0")
            venues.append(
                {
                    "venue": str(venue),
                    "status": "active",
                    "total_volume": str(v_stake),
                    "roi_pct": str((v_pnl / v_stake * 100) if v_stake > 0 else Decimal("0")),
                    "total_bets": len(grp),
                }
            )

    return {"status": "ok", "client_id": client_id, "venues": venues}


def read_sports_positions(client_id: str) -> dict[str, object]:
    """Read current sports positions from positions snapshot in GCS.

    Position service writes to: positions/{date}/{account_key}/{snapshot_type}/
    via PATH_REGISTRY. For sports, account_key includes venue-specific positions.
    """
    prefix = f"positions/latest/{client_id}/sports/"
    logger.info("Reading sports positions: client=%s prefix=%s", client_id, prefix)

    try:
        source = get_data_source(routing_key="positions", prefix=prefix)
        result = source.read(format="parquet")
        if isinstance(result, pd.DataFrame) and not result.empty:
            df = result
            open_positions = []
            for _, row in df.iterrows():
                open_positions.append(
                    {
                        "bet_id": str(row.get("bet_id", "")),  # noqa: qg-empty-fallback
                        "venue": str(row.get("venue_key", row.get("venue", ""))),  # noqa: qg-empty-fallback
                        "market": str(row.get("market", row.get("fixture_id", ""))),  # noqa: qg-empty-fallback
                        "side": str(row.get("side", "")),  # noqa: qg-empty-fallback
                        "selection": str(row.get("selection", "")),  # noqa: qg-empty-fallback
                        "odds": str(row.get("odds", row.get("odds_at_placement", ""))),  # noqa: qg-empty-fallback
                        "stake": str(row.get("stake", "")),  # noqa: qg-empty-fallback
                        "status": str(row.get("status", row.get("settlement_status", "open"))),
                    }
                )
            total_exposure = (
                Decimal(str(df["stake"].sum())) if "stake" in df.columns else Decimal("0")
            )
            return {
                "status": "ok",
                "client_id": client_id,
                "open_positions": open_positions,
                "total_exposure": str(total_exposure),
                "pending_settlements": len(open_positions),
            }
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.info("No sports positions found: %s", exc)

    return {"status": "no_data", "client_id": client_id, "open_positions": []}


def read_sports_risk(client_id: str) -> dict[str, object]:
    """Read sports risk exposure from GCS JSON snapshots.

    Risk service writes to: risk/{client_id}/{date}/exposure_summary.json
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    prefix = f"risk/{client_id}/{today}/"
    logger.info("Reading sports risk: client=%s prefix=%s", client_id, prefix)

    try:
        source = get_data_source(routing_key="risk", prefix=prefix)
        result = source.read(format="json")
        if isinstance(result, dict):
            return {"status": "ok", "client_id": client_id, **result}
        if isinstance(result, str):
            data = json.loads(result)
            return {"status": "ok", "client_id": client_id, **data}
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.info("No sports risk data found: %s", exc)

    return {"status": "no_data", "client_id": client_id}
