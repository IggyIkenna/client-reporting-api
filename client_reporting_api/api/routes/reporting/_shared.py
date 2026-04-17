"""Shared state + JSON loader + client-id resolver for the reporting routes."""

from __future__ import annotations

import json
from pathlib import Path

from client_reporting_api.core.invoice_state import InvoiceStateManager
from client_reporting_api.core.trade_analytics import CLIENT_IDS

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "backfill"
state_mgr = InvoiceStateManager()


def _load_json(client_id: str, filename: str) -> list[dict[str, object]] | None:
    """Load a per-client JSON file from the backfill dir, or None if absent."""
    path = DATA_DIR / client_id / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _resolve_client_ids(client_ids: str) -> list[str]:
    """Parse the comma-separated query param into normalised client IDs."""
    if not client_ids:
        return list(CLIENT_IDS)
    return [c.strip().upper() for c in client_ids.split(",") if c.strip()]
