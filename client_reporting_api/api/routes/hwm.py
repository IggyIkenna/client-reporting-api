"""HWM crystallization timeline route — Phase 5.C2.

Route (under /api/v1/clients/{client_id}/):
  GET /hwm-timeline  — fee recognition events per share class, filtered by date range.

SSOT: plans/active/client_reporting_pnl_attribution_mvp_2026_05_10.md Phase 5.C2.
FeeRecognitionRow schema: unified_api_contracts.internal.domain.hwm_ledger.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.entitlement import _enforce_entitlement  # pyright: ignore[reportPrivateUsage]
from client_reporting_api.core.hwm_reader import read_fee_recognition_rows

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients/{client_id}", tags=["hwm"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _mock_hwm_timeline(client_id: str) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "client_id": client_id,
        "rows": [
            {
                "share_class_id": "USDT",
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-04-01T00:00:00+00:00",
                "recognition_type": "PERFORMANCE_FEE_CRYSTALLIZATION",
                "amount_usd": "5000.00",
                "recognized_at": "2026-04-01T00:01:00+00:00",
                "source_event_id": "mock-event-q1-2026-usdt",
            },
            {
                "share_class_id": "USDT",
                "period_start": "2026-04-01T00:00:00+00:00",
                "period_end": "2026-07-01T00:00:00+00:00",
                "recognition_type": "PERFORMANCE_FEE_CRYSTALLIZATION",
                "amount_usd": "3200.00",
                "recognized_at": now,
                "source_event_id": "mock-event-q2-2026-usdt",
            },
            {
                "share_class_id": "BTC",
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-04-01T00:00:00+00:00",
                "recognition_type": "PERFORMANCE_FEE_CRYSTALLIZATION",
                "amount_usd": "1200.00",
                "recognized_at": "2026-04-01T00:01:00+00:00",
                "source_event_id": "mock-event-q1-2026-btc",
            },
        ],
    }


def _hwm_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    out = []
    for row in rows:
        period_start = row.get("period_start")
        period_end = row.get("period_end")
        recognized_at = row.get("recognized_at")
        amount = row.get("amount_usd")
        out.append(
            {
                "share_class_id": row.get("share_class_id"),
                "period_start": str(period_start) if period_start else None,
                "period_end": str(period_end) if period_end else None,
                "recognition_type": row.get("recognition_type"),
                "amount_usd": str(amount) if amount is not None else None,
                "recognized_at": str(recognized_at) if recognized_at else None,
                "source_event_id": row.get("source_event_id"),
            }
        )
    return {"client_id": client_id, "rows": out}


DateFromQuery = Annotated[date | None, Query(description="Start date (inclusive) YYYY-MM-DD")]
DateToQuery = Annotated[date | None, Query(description="End date (inclusive) YYYY-MM-DD")]


@router.get("/hwm-timeline")
def get_client_hwm_timeline(
    client_id: str,
    auth: AuthDep,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
) -> dict[str, object]:
    """HWM crystallization events for a client.

    Returns FeeRecognitionRow records sorted by recognized_at.
    In mock mode, returns demo quarterly crystallization events.
    """
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_hwm_timeline(client_id)
    rows = read_fee_recognition_rows(client_id, date_from=date_from, date_to=date_to)
    return _hwm_from_rows(client_id, rows)
