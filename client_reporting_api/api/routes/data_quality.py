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
from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Depends
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.data_quality import compute_data_quality
from client_reporting_api.core.entitlement import enforce_entitlement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["data-quality"])

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

#: deployment-api base URL — the SAME unified alert ledger the deployment-ui
#: monitoring pane shows (CI/CD + vm_down + consolidator_down + worker_liveness +
#: git_health), reachable + public on Cloud Run. The legacy k8s ``alerting-service``
#: DNS did NOT resolve from Cloud Run (→ always "unavailable"). Prod default; the
#: per-env override is the P11.20 follow-up (an ``alerting_service_url`` /
#: ``deployment_api_url`` field on ``UnifiedCloudConfig`` — blocked on a UTL change).
_DEPLOYMENT_API_URL = "https://uts-shared-deployment-api-cldtjniqvq-an.a.run.app"

#: deployment-api severity tokens → the UI DataQualityAlert closed set.
_SEVERITY_MAP = {
    "critical": "critical",
    "error": "critical",
    "fatal": "critical",
    "warning": "warning",
    "warn": "warning",
    "info": "info",
}


def _map_alert(entry: dict[str, object], idx: int) -> dict[str, object]:
    """Project a deployment-api ledger entry to the UI DataQualityAlert shape.

    The unified ledger carries ``{kind, timestamp, repo, workflow_name, severity,
    conclusion, message, run_url, alert_class}``; the paper-trading panel's
    ``DataQualityAlert`` contract is ``{id, severity, title, detail, source,
    timestamp}`` — so map (never pass the raw shape through, which would render
    blank rows). Severity coerces to the closed set ``critical|warning|info``.
    """
    repo = str(entry.get("repo") or "")
    workflow = str(entry.get("workflow_name") or "")
    kind = str(entry.get("kind") or "alert")
    alert_class = str(entry.get("alert_class") or "")
    ts = str(entry.get("timestamp") or "")
    sev = _SEVERITY_MAP.get(str(entry.get("severity") or "info").lower(), "info")
    detail = str(entry.get("message") or entry.get("conclusion") or "")
    return {
        "id": f"{repo}:{workflow}:{ts}:{idx}" if (repo or workflow) else f"alert-{idx}",
        "severity": sev,
        "title": workflow or alert_class or kind,
        "detail": detail,
        "source": alert_class or repo or "ci",
        "timestamp": ts,
    }


def _live_alerts() -> tuple[list[dict[str, object]], str]:
    """Best-effort fetch of live VM/infra alerts; ``([], "unavailable")`` on failure.

    Reads the deployment-api unified alert ledger (same source the deployment-ui
    monitoring pane shows) so missing/incomplete-data + VM/infra events surface on
    the paper-trading panel. Any transport/HTTP/shape error degrades to an honest
    empty list + ``alerts_source="unavailable"`` — never 500s the whole panel.
    """
    try:
        resp = httpx.get(f"{_DEPLOYMENT_API_URL}/api/alerts", timeout=5.0)
        resp.raise_for_status()
        payload = cast(dict[str, object], resp.json())
        raw = cast(list[dict[str, object]], payload.get("alerts", []))
        return [_map_alert(e, i) for i, e in enumerate(raw)], "deployment-api"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("data-quality: deployment-api alerts unavailable: %s", exc)
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
