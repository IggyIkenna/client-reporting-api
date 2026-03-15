from datetime import UTC, datetime

from fastapi import APIRouter
from unified_config_interface import UnifiedCloudConfig

router = APIRouter()

_cloud_cfg = UnifiedCloudConfig()


def _data_freshness() -> dict[str, object]:
    """Return data freshness info for health endpoint.

    Placeholder — real implementation would check actual data timestamps.
    """
    return {
        "last_processed_date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "stale": False,
    }


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "client-reporting-api",
        "cloud_provider": _cloud_cfg.cloud_provider,
        "mock_mode": _cloud_cfg.cloud_mock_mode,
        "data_freshness": _data_freshness(),
    }


@router.get("/readiness")
async def readiness_check() -> dict[str, str]:
    """Readiness probe — returns 503 if service is not ready to handle requests."""
    return {"status": "ready", "service": "client-reporting-api"}
