"""GET /api/v1/clients/{client_id}/data-quality — paper-run honest-coverage + SSOT.

Powers the paper-trading dashboard's "what data is missing / incomplete" panel.
Merges, for a client's canonical paper run, the RUN lens with the deployment-api
SSOT (the SAME source the deployment-ui monitoring pane shows — a break is fixed
once, at the deployment-api, not per-consumer):

1. **run lens** — the run's honest-absence ``skipped_specs`` + ``run_manifest``
   coverage (:func:`compute_data_quality`): "what THIS run could drive".
2. **alerts** — the deployment-api unified alert ledger (P11.20).
3. **corpus lens** — the deployment-api data-status manifest 4-state per
   asset_group (P11.21): "what data EXISTS in the corpus" — the bars
   deployment-ui renders. Surfacing both lets the operator spot divergence
   (a cell captured-in-manifest but run-skipped = config-unmappable, vs no-data).

Both deployment-api reads are BEST-EFFORT: an unreachable deployment-api degrades
to ``alerts: []`` + ``manifest_coverage: {}`` with an ``*_source: "unavailable"``
flag — a missing SSOT feed never fails the whole panel.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core import deployment_api_client
from client_reporting_api.core.data_quality import compute_data_quality
from client_reporting_api.core.entitlement import enforce_entitlement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["data-quality"])

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

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
    """Live VM/infra alerts from the deployment-api SSOT, mapped to the UI shape."""
    raw, source = deployment_api_client.get_unified_alerts()
    return [_map_alert(e, i) for i, e in enumerate(raw)], source


def _manifest_coverage() -> tuple[list[dict[str, object]], str]:
    """Corpus manifest 4-state per asset_group from the deployment-api data-status SSOT.

    Returns a LIST of rows (the UI contract — never a dict, which would crash a
    ``.map``) sorted by asset_group, each carrying the 4-state counts +
    ``coverage_pct`` exactly as the deployment-ui data-status bars show them.
    """
    by_ag, source = deployment_api_client.get_data_status_coverage()
    rows: list[dict[str, object]] = [
        {
            "asset_group": ag,
            "captured": v.get("captured", 0),
            "empty_confirmed": v.get("empty_confirmed", 0),
            "attempted_failed": v.get("attempted_failed", 0),
            "expected_unattempted": v.get("expected_unattempted", 0),
            "total": v.get("total", 0),
            "coverage_pct": v.get("coverage_pct", 0),
        }
        for ag, v in sorted(by_ag.items())
    ]
    return rows, source


@router.get("/{client_id}/data-quality")
def get_data_quality(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Return the run lens + deployment-api SSOT lens (alerts + corpus coverage).

    Single-client scope (``enforce_entitlement`` — funds/data never cross clients).
    """
    enforce_entitlement(auth, client_id)

    dq = compute_data_quality(client_id)
    alerts, alerts_source = _live_alerts()
    manifest_coverage, manifest_source = _manifest_coverage()

    return {
        "run_id": dq["run_id"],
        "coverage": dq["coverage"],
        "skipped": dq["skipped"],
        # P11.22 — the drivable-but-thin specs (ran on a sparse window, below the
        # min-window-coverage threshold), with each one's coverage % vs its threshold,
        # so the panel FLAGS a thin run instead of silently trusting it like a full one.
        "thin_specs": dq["thin_specs"],
        "note": dq["note"],
        "alerts": alerts,
        "alerts_source": alerts_source,
        # P11.21 — corpus manifest 4-state per asset_group, the deployment-api
        # data-status SSOT the deployment-ui bars render (run lens vs corpus lens).
        "manifest_coverage": manifest_coverage,
        "manifest_source": manifest_source,
        "generated_utc": datetime.now(UTC).isoformat(),
    }
