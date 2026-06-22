"""GET /api/v1/clients/{client_id}/data-quality — paper-run honest-coverage + live alerts.

Powers the paper-trading dashboard's "what data is missing / incomplete" panel.
Merges TWO real sources for a client's canonical paper run:

1. the run's honest-absence ``skipped_specs`` + ``run_manifest`` coverage
   (:func:`compute_data_quality`), and
2. the live VM alert stream proxied from the alerting-service (the same source the
   ``/alerts`` route uses).

Alerts are BEST-EFFORT: if the alerting-service is unreachable the endpoint still
returns the run data-quality with ``alerts: []`` + ``alerts_source: "unavailable"``
— a missing alert stream never fails the whole panel.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.alerts_client import get_alerts
from client_reporting_api.core.data_quality import compute_data_quality
from client_reporting_api.core.entitlement import enforce_entitlement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["data-quality"])

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

#: Default alerting-service URL — same canonical k8s DNS the ``/alerts`` route uses.
_ALERTING_SERVICE_URL = "http://alerting-service:8080"


def _live_alerts() -> tuple[list[dict[str, object]], str]:
    """Best-effort fetch of live VM alerts; ``([], "unavailable")`` on any failure.

    The data-quality panel must surface streamed VM events (missing/incomplete
    data, etc.) alongside the run coverage — but an alerting-service outage must
    NOT 500 the panel, so every transport/HTTP error degrades to an honest empty
    list + ``alerts_source="unavailable"``.
    """
    try:
        return get_alerts(_ALERTING_SERVICE_URL), "alerting-service"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("data-quality: alerting-service unavailable: %s", exc)
        return [], "unavailable"


@router.get("/{client_id}/data-quality")
def get_data_quality(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Return the canonical paper run's honest coverage + skipped specs + live alerts.

    Single-client scope (``enforce_entitlement`` — funds/data never cross clients).
    """
    enforce_entitlement(auth, client_id)

    dq = compute_data_quality(client_id)
    alerts, alerts_source = _live_alerts()

    return {
        "run_id": dq["run_id"],
        "coverage": dq["coverage"],
        "skipped": dq["skipped"],
        "note": dq["note"],
        "alerts": alerts,
        "alerts_source": alerts_source,
        "generated_utc": datetime.now(UTC).isoformat(),
    }
