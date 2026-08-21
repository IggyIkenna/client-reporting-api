"""GET /alerts — proxy to alerting-service."""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.alerts_client import get_alerts
from client_reporting_api.core.entitlement import require_internal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alerts"])

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

# Default alerting-service URL: overridable via the ALERTING_SERVICE_URL env var.
# Kubernetes service DNS is the canonical default; local dev can override.
_ALERTING_SERVICE_URL = "http://alerting-service:8080"


@router.get("/alerts")
def proxy_alerts(auth: AuthDep) -> list[dict[str, object]]:
    """Proxy alerts from alerting-service.

    Entitlement: the alerting-service proxy call carries no client_id
    filter at all (``get_alerts`` takes only the base URL), so there is
    no way to scope this to one client's alerts today —
    ``require_internal`` is the strictest plausible gate (2026-08-21
    CTO handoff P1 fix, judgment call documented in
    ``walkthrough_feedback_remediation_2026_08_21.md``).
    """
    require_internal(auth)
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
