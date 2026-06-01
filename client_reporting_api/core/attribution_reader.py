"""Attribution parquet reader for Phase 4 client-reporting-api routes.

Reads PnLAttributionRow parquets emitted by UTL emit_attribution_parquet.
Blob path format: pnl_attribution/strategy_id={S}/client_id={C}/date={D}/rows.parquet

SSOT: plans/active/client_reporting_pnl_attribution_mvp_2026_05_10.md Phase 4.
Bucket SSOT: deployment-service/configs/cloud-providers.yaml kind=client-reports
"""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import cast

import pyarrow.parquet as pq
from unified_trading_library import get_storage_client, resolve_bucket_name
from unified_trading_library.cloud_interface.bucket_naming import Cloud  # noqa: qg-deep-import

logger = logging.getLogger(__name__)

_PREFIX = "pnl_attribution/"


def _blob_date(blob_path: str) -> date | None:
    for segment in blob_path.split("/"):
        if segment.startswith("date="):
            try:
                return date.fromisoformat(segment[5:])
            except ValueError:
                return None
    return None


def read_attribution_rows(
    client_id: str,
    date_from: date | None = None,
    date_to: date | None = None,
    cloud: str = "gcp",
) -> list[dict[str, object]]:
    """Read PnLAttributionRow parquet shards for a client.

    Scans all blobs under pnl_attribution/ that match client_id={client_id}.
    Filters by date range when provided.  Returns raw pylist dicts matching
    the parquet schema (factor/layer stored as string values).

    Returns empty list when the bucket has no matching shards (no data yet).
    """
    cloud_typed: Cloud = cast(Cloud, cloud)
    bucket = resolve_bucket_name(cloud=cloud_typed, kind="client-reports")
    storage = get_storage_client()

    rows: list[dict[str, object]] = []
    target_segment = f"client_id={client_id}/"

    try:
        for blob_meta in storage.list_blobs(bucket, prefix=_PREFIX):
            path = blob_meta.name
            if target_segment not in path:
                continue
            if not path.endswith("/rows.parquet"):
                continue

            blob_dt = _blob_date(path)
            if date_from and blob_dt and blob_dt < date_from:
                continue
            if date_to and blob_dt and blob_dt > date_to:
                continue

            data = storage.download_bytes(bucket, path)
            table = pq.read_table(io.BytesIO(data))
            rows.extend(table.to_pylist())

    except Exception as exc:
        logger.warning("attribution_reader: bucket scan failed for client=%s: %s", client_id, exc)

    return rows
