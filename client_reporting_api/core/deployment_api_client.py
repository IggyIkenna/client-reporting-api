"""Typed HTTP client for the deployment-api — the SSOT the deployment-ui shows.

The paper-trading data-quality panel surfaces TWO things that the operator's
deployment-ui monitoring pane already owns, so it must read them from the SAME
source (a break is then fixed once, at the deployment-api, not per-consumer):

- **alerts** — the unified alert ledger (``GET /api/alerts``): CI/CD + vm_down +
  consolidator_down + worker_liveness + git_health.
- **data-status coverage** — the corpus-wide manifest 4-state
  (``GET /api/data-status/honest-coverage``): per-asset-group
  ``captured`` / ``empty_confirmed`` / ``attempted_failed`` /
  ``expected_unattempted`` / ``coverage_pct`` — the bars deployment-ui renders.

Both deployment-api endpoints are reachable + public on Cloud Run. Every call is
BEST-EFFORT: any transport/HTTP/shape error degrades to an empty result so a
deployment-api blip never 500s the paper-trading panel.

SSOT: codex/02-data/availability-manifest-and-data-status.md (manifest 4-state) +
plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P11.20/P11.21).
"""

from __future__ import annotations

import logging
from typing import cast

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5.0


def _base_url() -> str:
    """deployment-api base URL from ``UnifiedCloudConfig`` (per-env typed field; no raw env read).

    P11.21-polish (A4): the address is the ``deployment_api_url`` field on
    ``UnifiedCloudConfig`` (UTL) — the SAME source the deployment-ui monitoring pane
    reads — so a per-env override is a typed config value the consumers share, never a
    hardcoded constant. Used by BOTH the alerts feed (P11.20) and the data-status SSOT
    cross-reference (P11.21).
    """
    from client_reporting_api.config import get_config

    return get_config().deployment_api_url


def get_unified_alerts() -> tuple[list[dict[str, object]], str]:
    """Fetch the unified alert ledger; ``([], "unavailable")`` on any failure.

    Returns ``(alerts, source)`` where ``source`` is ``"deployment-api"`` on a
    successful fetch (even with zero alerts — a clean fleet) or ``"unavailable"``
    when the deployment-api could not be reached.
    """
    try:
        resp = httpx.get(f"{_base_url()}/api/alerts", timeout=_TIMEOUT_S)
        resp.raise_for_status()
        payload = cast(dict[str, object], resp.json())
        alerts = cast(list[dict[str, object]], payload.get("alerts", []))
        return alerts, "deployment-api"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("deployment_api_client: alerts unavailable: %s", exc)
        return [], "unavailable"


def get_data_status_coverage() -> tuple[dict[str, dict[str, object]], str]:
    """Fetch the corpus manifest 4-state per asset_group; ``({}, "unavailable")`` on failure.

    Returns ``(by_asset_group, source)`` where ``by_asset_group`` maps each
    asset_group → ``{captured, empty_confirmed, attempted_failed,
    expected_unattempted, total, coverage_pct, all_shards_coverage_pct}`` — the
    SAME manifest 4-state the deployment-ui data-status bars render, so the paper
    panel's corpus-coverage lens is in sync with the operator's data-status page.
    """
    try:
        resp = httpx.get(f"{_base_url()}/api/data-status/honest-coverage", timeout=_TIMEOUT_S)
        resp.raise_for_status()
        payload = cast(dict[str, object], resp.json())
        by_ag = cast(dict[str, dict[str, object]], payload.get("by_asset_group", {}))
        return by_ag, "deployment-api"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("deployment_api_client: data-status coverage unavailable: %s", exc)
        return {}, "unavailable"
