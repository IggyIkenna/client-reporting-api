"""Fee recognition parquet reader for HWM crystallization timeline (Phase 5.C2).

Reads FeeRecognitionRow parquets emitted by UTL HWMCrystallizer.
Blob path: {client_id}/fee_recognition/{YYYY-MM-DD}/*.parquet

SSOT: plans/active/client_reporting_pnl_attribution_mvp_2026_05_10.md Phase 5.C2
Bucket SSOT: deployment-service/configs/cloud-providers.yaml kind=client-statements
"""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import cast

import pyarrow.parquet as pq
from unified_trading_library.cloud_interface import get_storage_client  # noqa: qg-deep-import
from unified_trading_library.cloud_interface.bucket_naming import (  # noqa: qg-deep-import
    Cloud,
    resolve_bucket_name,
)

logger = logging.getLogger(__name__)

_FEE_RECOGNITION_PREFIX = "fee_recognition/"


def _blob_date(blob_path: str) -> date | None:
    for segment in blob_path.split("/"):
        raw = segment[:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return None


def read_fee_recognition_rows(
    client_id: str,
    date_from: date | None = None,
    date_to: date | None = None,
    cloud: str = "gcp",
) -> list[dict[str, object]]:
    """Read FeeRecognitionRow parquet shards for a client.

    Scans blobs under {client_id}/fee_recognition/ in the client-statements bucket.
    Filters by date range when provided. Returns raw pylist dicts.

    Returns empty list when no matching shards exist.
    """
    cloud_typed: Cloud = cast(Cloud, cloud)
    bucket = resolve_bucket_name(cloud=cloud_typed, kind="client-statements")
    storage = get_storage_client()

    rows: list[dict[str, object]] = []
    prefix = f"{client_id}/{_FEE_RECOGNITION_PREFIX}"

    try:
        for blob_meta in storage.list_blobs(bucket, prefix=prefix):
            path = blob_meta.name
            if not path.endswith(".parquet"):
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
        logger.warning("hwm_reader: fee_recognition scan failed for client=%s: %s", client_id, exc)

    return rows
