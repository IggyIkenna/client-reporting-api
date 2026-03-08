from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "client-reporting-api"}


@router.get("/readiness")
async def readiness_check() -> dict[str, str]:
    """Readiness probe — returns 503 if service is not ready to handle requests."""
    return {"status": "ready", "service": "client-reporting-api"}
