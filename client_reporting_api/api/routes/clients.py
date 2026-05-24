"""Client list API routes — powers the client selector in the UI.

Supports filtering by organisation_id and strategy_id.
Internal users see all clients; external users see only their org.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.entitlement import (
    _enforce_entitlement,  # pyright: ignore[reportPrivateUsage]
    require_internal,
)
from client_reporting_api.core.mock_performance_data import MOCK_CLIENTS
from client_reporting_api.core.tranche_router import load_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["clients"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _build_client_entry(
    cid: str,
    cfg: dict[str, str | float | bool | dict[str, float]],
    registry: dict[str, dict[str, str | float | bool | dict[str, float]]],
) -> dict[str, str | bool]:
    """Build a client entry with org and strategy metadata."""
    org_id = str(cfg.get("organisation_id", ""))
    strategy_id = str(cfg.get("strategy_id", ""))

    # Resolve org name from registry
    orgs = cast(dict[str, Any], registry.get("organisations", {}))
    org_info = cast(dict[str, Any], orgs.get(org_id, {}))
    org_name = str(org_info.get("name", org_id))
    org_type = str(org_info.get("type", "client"))

    # Resolve strategy name from registry
    strategies = cast(dict[str, Any], registry.get("strategies", {}))
    strat_info = cast(dict[str, Any], strategies.get(strategy_id, {}))
    strategy_name = str(strat_info.get("name", strategy_id))

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


def _filter_clients(
    clients_cfg: dict[str, object],
    registry: dict[str, object],
    organisation_id: str | None,
    strategy_id: str | None,
) -> list[dict[str, str | bool]]:
    """Build the filtered client entry list for ``list_clients``."""
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
    return clients


def _organisation_list(registry: dict[str, object]) -> list[dict[str, str]]:
    """Project the registry organisations dict to a UI-friendly list."""
    orgs_raw = registry.get("organisations", {})
    if not isinstance(orgs_raw, dict):
        return []
    return [
        {
            "id": oid,
            "name": str(oinfo.get("name", oid)),
            "type": str(oinfo.get("type", "client")),
        }
        for oid, oinfo in orgs_raw.items()
        if isinstance(oinfo, dict)
    ]


def _strategy_list(registry: dict[str, object]) -> list[dict[str, str]]:
    """Project the registry strategies dict to a UI-friendly list."""
    strats_raw = registry.get("strategies", {})
    if not isinstance(strats_raw, dict):
        return []
    return [
        {
            "id": sid,
            "name": str(sinfo.get("name", sid)),
            "description": str(sinfo.get("description", "")),
        }
        for sid, sinfo in strats_raw.items()
        if isinstance(sinfo, dict)
    ]


@router.get("")
def list_clients(
    auth: AuthDep,
    organisation_id: str | None = Query(None, description="Filter by organisation"),
    strategy_id: str | None = Query(None, description="Filter by strategy"),
) -> dict[str, list[dict[str, str | bool]] | list[dict[str, str]]]:
    """Return all active clients with org/strategy grouping.

    Listing every client is a cross-tenant operation so the endpoint is
    internal-only. External callers should use ``GET /api/v1/clients/{client_id}``
    for their own client — that route runs ``_enforce_entitlement``.
    """
    require_internal(auth)
    if _cloud_cfg.is_mock_mode():
        return {"clients": MOCK_CLIENTS, "organisations": [], "strategies": []}

    registry = load_registry()
    clients_cfg = registry.get("clients", {})
    if not isinstance(clients_cfg, dict):
        clients_cfg = {}

    return {
        "clients": _filter_clients(clients_cfg, registry, organisation_id, strategy_id),
        "organisations": _organisation_list(registry),
        "strategies": _strategy_list(registry),
    }


@router.get("/{client_id}")
def get_client(client_id: str, auth: AuthDep) -> dict[str, str | bool]:
    """Return config for a single client."""
    _enforce_entitlement(auth, client_id)
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
