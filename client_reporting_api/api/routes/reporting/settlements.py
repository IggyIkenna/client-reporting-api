"""GET /settlements — settlement/reconciliation data from real trade history."""

from __future__ import annotations

from fastapi import APIRouter, Query

from client_reporting_api.api.routes.reporting._shared import (
    _load_json,
    _resolve_client_ids,
    state_mgr,
)
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()


def _trade_to_settlement(trade: dict[str, object], venue: str) -> dict[str, object]:
    """Project one raw trade into a settlement row (CEX trades settle instantly)."""
    dt_str = str(trade.get("datetime", ""))[:10]
    cost = float(trade.get("cost", 0) or 0)
    return {
        "id": str(trade.get("id", "")),
        "date": dt_str,
        "venue": venue,
        "instrument": str(trade.get("symbol", "")),
        "side": str(trade.get("side", "")),
        "expectedAmount": cost,
        "settledAmount": cost,
        "status": "settled",
        "settlementDate": dt_str,
    }


def _client_settlements(cid: str) -> list[dict[str, object]]:
    """Return the last 50 trade-derived settlements for one client."""
    trades = _load_json(cid, "trades.json") or []
    registry = load_registry()
    client_cfg = registry.get("clients", {}).get(cid, {})
    venue = str(client_cfg.get("venue", "")) if isinstance(client_cfg, dict) else ""
    return [_trade_to_settlement(t, venue) for t in trades[-50:]]


def _invoices_simple() -> list[dict[str, object]]:
    """Flatten all invoices into the simple status/amount shape used here."""
    return [
        {
            "id": inv.get("invoice_id", ""),
            "client": inv.get("client_id", ""),
            "amount": float(inv.get("amount", 0)),
            "status": "paid" if inv.get("paid") else "unpaid",
            "date": str(inv.get("date", "")),
        }
        for inv in state_mgr.get_invoices()
    ]


@router.get("/settlements")
def get_settlements(
    client_ids: str = Query(default="", description="Comma-separated client IDs"),
) -> dict[str, object]:
    """Return settlement/reconciliation data from real trade history."""
    ids = _resolve_client_ids(client_ids)
    settlements: list[dict[str, object]] = []
    for cid in ids:
        settlements.extend(_client_settlements(cid))

    return {
        "data": settlements,
        "settlements": settlements,
        "invoices": _invoices_simple(),
    }
