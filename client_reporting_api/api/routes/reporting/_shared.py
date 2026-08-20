# pyright: reportUnusedFunction=false
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


def _vehicle_type_for_client(cfg: dict[str, object]) -> str:
    """Classify a client's investment vehicle for cross-client reporting aggregates.

    ``client-reporting-api`` has no direct join to UAC's
    ``ClientRegistry.vehicle_type`` (disjoint client_id namespaces — this
    service's ids are the ``credentials-registry.yaml`` tranche keys, e.g.
    "PR"/"IK", not UAC's "acme-fund"/"patrick-elysium"). The equivalent signal
    already on ``ClientConfig`` is ``is_pooled``: a pooled account (multiple
    investors sharing one NAV pool via ``pool_investors``) is the reporting
    analogue of UAC's "fund" vehicle; a direct single-investor managed account
    is the analogue of "sma". Shared by every reporting route that pools
    figures across clients (nav.py, fund_operations.py) so a vehicle-type
    breakdown stays consistent everywhere the same blind spot could recur —
    see plans/archive/2026_08/client_reporting_api_nav_aggregation_vehicle_type_blind_2026_08_20.md.
    """
    return "fund" if cfg.get("is_pooled") else "sma"
