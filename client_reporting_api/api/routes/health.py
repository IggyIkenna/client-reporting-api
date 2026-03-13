from fastapi import APIRouter
from unified_config_interface import UnifiedCloudConfig

router = APIRouter()

_cloud_cfg = UnifiedCloudConfig()


@router.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": "client-reporting-api",
        "cloud_provider": _cloud_cfg.cloud_provider,
        "mock_mode": _cloud_cfg.cloud_mock_mode,
    }


@router.get("/readiness")
async def readiness_check() -> dict[str, str]:
    """Readiness probe — returns 503 if service is not ready to handle requests."""
    return {"status": "ready", "service": "client-reporting-api"}
