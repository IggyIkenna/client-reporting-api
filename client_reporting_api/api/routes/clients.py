"""Client list API routes — powers the client selector in the UI.

Supports filtering by organisation_id and strategy_id.
Internal users see all clients; external users see only their org.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.core.mock_performance_data import MOCK_CLIENTS
from client_reporting_api.core.tranche_router import load_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["clients"])

_cloud_cfg = UnifiedCloudConfig()


def _build_client_entry(
    cid: str,
    cfg: dict[str, str | float | bool | dict[str, float]],
    registry: dict[str, dict[str, str | float | bool | dict[str, float]]],
) -> dict[str, str | bool]:
    """Build a client entry with org and strategy metadata."""
    org_id = str(cfg.get("organisation_id", ""))
    strategy_id = str(cfg.get("strategy_id", ""))

    # Resolve org name from registry
    orgs = registry.get("organisations", {})
    org_info = orgs.get(org_id, {}) if isinstance(orgs, dict) else {}
    org_name = str(org_info.get("name", org_id)) if isinstance(org_info, dict) else org_id
    org_type = str(org_info.get("type", "client")) if isinstance(org_info, dict) else "client"

    # Resolve strategy name from registry
    strategies = registry.get("strategies", {})
    strat_info = strategies.get(strategy_id, {}) if isinstance(strategies, dict) else {}
    strategy_name = (
        str(strat_info.get("name", strategy_id)) if isinstance(strat_info, dict) else strategy_id
    )

    return {
        "id": cid,
        "name": str(cfg.get("full_name", cid)),
        "venue": str(cfg.get("venue", "")),
        "currency": str(cfg.get("currency", "")),
        "tranche": str(cfg.get("tranche", "")),
        "is_active": bool(cfg.get("is_active", False)),
        "is_underwater": bool(cfg.get("is_underwater", False)),
        "organisation_id": org_id,
        "organisation_name": org_name,
        "organisation_type": org_type,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
    }


@router.get("")
def list_clients(
    organisation_id: str | None = Query(None, description="Filter by organisation"),
    strategy_id: str | None = Query(None, description="Filter by strategy"),
) -> dict[str, list[dict[str, str | bool]] | list[dict[str, str]]]:
    """Return all active clients with org/strategy grouping.

    Internal users see all; pass organisation_id to filter for a specific org.
    """
    if _cloud_cfg.is_mock_mode():
        return {"clients": MOCK_CLIENTS, "organisations": [], "strategies": []}

    registry = load_registry()
    clients_cfg = registry.get("clients", {})

    clients: list[dict[str, str | bool]] = []
    for cid, cfg in clients_cfg.items():
        if not isinstance(cfg, dict):
            continue
        entry = _build_client_entry(cid, cfg, registry)
        if organisation_id and entry.get("organisation_id") != organisation_id:
            continue
        if strategy_id and entry.get("strategy_id") != strategy_id:
            continue
        clients.append(entry)

    # Build org and strategy lists for UI grouping
    orgs_raw = registry.get("organisations", {})
    org_list: list[dict[str, str]] = []
    if isinstance(orgs_raw, dict):
        for oid, oinfo in orgs_raw.items():
            if isinstance(oinfo, dict):
                org_list.append(
                    {
                        "id": oid,
                        "name": str(oinfo.get("name", oid)),
                        "type": str(oinfo.get("type", "client")),
                    }
                )

    strats_raw = registry.get("strategies", {})
    strat_list: list[dict[str, str]] = []
    if isinstance(strats_raw, dict):
        for sid, sinfo in strats_raw.items():
            if isinstance(sinfo, dict):
                strat_list.append(
                    {
                        "id": sid,
                        "name": str(sinfo.get("name", sid)),
                        "description": str(sinfo.get("description", "")),
                    }
                )

    return {
        "clients": clients,
        "organisations": org_list,
        "strategies": strat_list,
    }


@router.get("/{client_id}")
def get_client(client_id: str) -> dict[str, str | bool]:
    """Return config for a single client."""
    if _cloud_cfg.is_mock_mode():
        for c in MOCK_CLIENTS:
            if c["id"] == client_id:
                return c
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    registry = load_registry()
    clients_cfg = registry.get("clients", {})
    cfg = clients_cfg.get(client_id)
    if cfg is None or not isinstance(cfg, dict):
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    return _build_client_entry(client_id, cfg, registry)
