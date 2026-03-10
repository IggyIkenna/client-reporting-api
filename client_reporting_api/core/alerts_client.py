"""HTTP proxy client for alerting-service /alerts."""

from __future__ import annotations

import logging
from typing import cast

import httpx

logger = logging.getLogger(__name__)


def get_alerts(alerting_service_url: str) -> list[dict[str, object]]:
    """Proxy GET /alerts from alerting-service.

    Args:
        alerting_service_url: Base URL of the alerting service, e.g. "http://alerting-service:8080".

    Returns:
        List of alert dicts as returned by the alerting service.
    """
    url = f"{alerting_service_url}/alerts"
    logger.info("Proxying GET %s", url)
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    data = cast(list[dict[str, object]], resp.json())
    return data
