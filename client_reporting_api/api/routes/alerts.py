"""GET /alerts — proxy to alerting-service."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from client_reporting_api.core.alerts_client import get_alerts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alerts"])

# Default alerting-service URL: overridable via the ALERTING_SERVICE_URL env var.
# Kubernetes service DNS is the canonical default; local dev can override.
_ALERTING_SERVICE_URL = "http://alerting-service:8080"


@router.get("/alerts")
def proxy_alerts() -> list[dict[str, object]]:
    """Proxy alerts from alerting-service."""
    logger.info("proxy_alerts: forwarding to %s/alerts", _ALERTING_SERVICE_URL)
    try:
        return get_alerts(_ALERTING_SERVICE_URL)
    except httpx.HTTPStatusError as exc:
        logger.error("alerting-service returned error: %s", exc)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="alerting-service error",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Failed to reach alerting-service: %s", exc)
        raise HTTPException(status_code=503, detail="alerting-service unavailable") from exc
