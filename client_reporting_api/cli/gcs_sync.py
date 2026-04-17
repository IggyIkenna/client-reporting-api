"""GCS persistence for client-reporting CLI updates.

MiFID II requires 5-year retention of order and trade records. GCS object
versioning provides the immutable audit trail. All cloud access goes through
unified-trading-library storage helpers — no direct google.cloud imports.
"""

from __future__ import annotations

import logging
from pathlib import Path

from unified_trading_library import (  # pyright: ignore[reportPrivateImportUsage]
    download_from_storage,
    storage_exists,
    upload_to_storage,
)

from client_reporting_api.config import get_config

logger = logging.getLogger(__name__)

_GCS_BUCKET: str | None = None

_SYNC_FILES: tuple[str, ...] = (
    "equity_curve.json",
    "orders.json",
    "trades.json",
    "positions.json",
    "balance.json",
    "summary.json",
    "transfers.json",
    "bills_ledger.json",
)


def _get_gcs_bucket() -> str | None:
    """Return the configured GCS bucket for client reporting, caching the lookup."""
    global _GCS_BUCKET
    if _GCS_BUCKET is not None:
        return _GCS_BUCKET if _GCS_BUCKET else None
    try:
        cfg = get_config()
        if cfg.client_data_bucket:
            _GCS_BUCKET = cfg.client_data_bucket
            logger.info("GCS bucket: %s", _GCS_BUCKET)
            return _GCS_BUCKET
    except Exception as exc:
        logger.warning("Config-based bucket lookup failed: %s", str(exc))
    _GCS_BUCKET = ""
    return None


def _download_from_gcs(client_id: str, client_dir: Path) -> bool:
    """Bootstrap a client's backfill directory from GCS when the local copy is absent.

    Cloud Run Jobs start with an empty filesystem — we must bootstrap from GCS
    before doing an incremental update. Also handles any environment where
    local data was lost (machine swap, container restart).
    """
    equity_path = client_dir / "equity_curve.json"
    if equity_path.exists():
        return True  # Already have local data

    bucket_name = _get_gcs_bucket()
    if not bucket_name:
        return False

    try:
        client_dir.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for fname in _SYNC_FILES:
            object_path = f"backfill/{client_id}/{fname}"
            if storage_exists(bucket_name, object_path):
                blob = download_from_storage(bucket_name, object_path)
                (client_dir / fname).write_bytes(blob)
                downloaded += 1

        if downloaded > 0:
            logger.info("[%s] Downloaded %d files from GCS", client_id, downloaded)
            return equity_path.exists()
        logger.warning("[%s] No data in GCS — needs initial backfill", client_id)
        return False
    except Exception as exc:
        logger.warning("[%s] GCS download failed: %s", client_id, str(exc))
        return False


def _sync_to_gcs(client_id: str, client_dir: Path) -> None:
    """Upload client data files to GCS for durable persistence.

    MiFID II requires 5-year retention of order and trade records.
    GCS object versioning provides the immutable audit trail.
    """
    bucket_name = _get_gcs_bucket()
    if not bucket_name:
        return  # Local-only mode (dev), skip GCS sync

    try:
        synced = 0
        for fname in _SYNC_FILES:
            fpath = client_dir / fname
            if fpath.exists():
                upload_to_storage(bucket_name, f"backfill/{client_id}/{fname}", fpath.read_bytes())
                synced += 1

        if synced > 0:
            logger.debug(
                "[%s] Synced %d files to gs://%s/backfill/%s/",
                client_id,
                synced,
                bucket_name,
                client_id,
            )
    except Exception as exc:
        # GCS sync is best-effort — don't fail the update if GCS is unavailable
        logger.warning("[%s] GCS sync failed (data still local): %s", client_id, str(exc))
