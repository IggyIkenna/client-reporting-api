"""PnL report generation from GCS Parquet data via UCI DataSource."""

from __future__ import annotations

import logging
from typing import cast

import pandas as pd
from unified_trading_library import DataSource, get_data_source

logger = logging.getLogger(__name__)

# Routing key for PnL data — UCI resolves the target bucket via
# PROTOCOL_DATA_SOURCE_BUCKET_PNL env var (or PROTOCOL_DATA_SOURCE_BUCKET fallback).
_PNL_ROUTING_KEY = "pnl"


def _read_pnl_dataframe(source: DataSource) -> pd.DataFrame:
    """Read PnL data from the DataSource; return empty DataFrame on missing data."""
    try:
        result = source.read(format="parquet")
        if isinstance(result, pd.DataFrame):
            return result
        logger.warning("DataSource.read returned unexpected type: %s", type(result))
        return pd.DataFrame()
    except FileNotFoundError:
        logger.info("No PnL data found at the configured path")
        return pd.DataFrame()


def generate_pnl_report(
    client_id: str,
    period_month: str,
) -> dict[str, object]:
    """Read pnl/{period_month}/{client_id}/ from GCS and return report dict.

    The target bucket is resolved by UCI from the PROTOCOL_DATA_SOURCE_BUCKET_PNL
    environment variable (or PROTOCOL_DATA_SOURCE_BUCKET as fallback).

    Args:
        client_id: The client identifier.
        period_month: Month in "YYYY-MM" format.

    Returns:
        A dict containing status, client_id, period_month, and rows of PnL data.
    """
    prefix = f"pnl/{period_month}/{client_id}/"
    logger.info(
        "Generating PnL report: client=%s period=%s prefix=%s", client_id, period_month, prefix
    )

    source = get_data_source(routing_key=_PNL_ROUTING_KEY, prefix=prefix)
    df = _read_pnl_dataframe(source)

    if df.empty:
        logger.warning("No PnL data found for client=%s period=%s", client_id, period_month)
        return {
            "status": "no_data",
            "client_id": client_id,
            "period_month": period_month,
            "rows": [],
        }

    records = cast(list[dict[str, object]], df.to_dict(orient="records"))
    return {
        "status": "ok",
        "client_id": client_id,
        "period_month": period_month,
        "rows": records,
    }
