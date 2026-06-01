# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnnecessaryCast=false
"""GET /clients — client / organisation / strategy dropdown data."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from unified_api_contracts.internal import ClientConfig
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.entitlement import require_internal
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _client_entry_for_listing(
    cid: str,
    cfg: ClientConfig,
    orgs_raw: object,
    strats_raw: object,
) -> dict[str, object]:
    """Project a client config row to the dict shape the UI selector wants."""
    cfg_dict = cast(dict[str, object], cfg)
    org_id = str(cfg_dict.get("organisation_id", ""))
    orgs_dict = cast(dict[str, Any], orgs_raw) if isinstance(orgs_raw, dict) else {}
    org_info: dict[str, Any] = orgs_dict.get(org_id, {})
    strat_id = str(cfg_dict.get("strategy_id", ""))
    strats_dict = cast(dict[str, Any], strats_raw) if isinstance(strats_raw, dict) else {}
    strat_info: dict[str, Any] = strats_dict.get(strat_id, {})
    return {
        "id": cid,
        "name": str(cfg_dict.get("full_name", cid)),
        "venue": str(cfg_dict.get("venue", "")),
        "currency": str(cfg_dict.get("currency", "")),
        "tranche": str(cfg_dict.get("tranche", "")),
        "is_active": bool(cfg_dict.get("is_active", False)),
        "is_underwater": bool(cfg_dict.get("is_underwater", False)),
        "organisation_id": org_id,
        "organisation_name": (str(org_info.get("name", org_id)) if isinstance(org_info, dict) else org_id),  # pyright: ignore[reportUnnecessaryIsInstance]
        "organisation_type": (str(org_info.get("type", "client")) if isinstance(org_info, dict) else "client"),  # pyright: ignore[reportUnnecessaryIsInstance]
        "strategy_id": strat_id,
        "strategy_name": (str(strat_info.get("name", strat_id)) if isinstance(strat_info, dict) else strat_id),  # pyright: ignore[reportUnnecessaryIsInstance]
    }


def _project_orgs_for_listing(orgs_raw: object) -> list[dict[str, object]]:
    """Flatten the organisations section of the registry for the UI."""
    if not isinstance(orgs_raw, dict):
        return []
    return [
        {
            "id": oid,
            "name": str(cast(dict[str, Any], oinfo).get("name", oid)),
            "type": str(cast(dict[str, Any], oinfo).get("type", "client")),
        }
        for oid, oinfo in cast(dict[str, Any], orgs_raw).items()
        if isinstance(oinfo, dict)
    ]


def _project_strats_for_listing(strats_raw: object) -> list[dict[str, object]]:
    """Flatten the strategies section of the registry for the UI."""
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


@router.get("/clients")
def get_clients(auth: AuthDep) -> dict[str, list[dict[str, object]]]:
    """Return all clients, organisations, and strategies from credentials-registry.

    Cross-tenant listing — internal-only.
    """
    require_internal(auth)
    registry = load_registry()
    clients_cfg = registry.get("clients", {})
    orgs_raw = registry.get("organisations", {})
    strats_raw = registry.get("strategies", {})

    clients: list[dict[str, object]] = [
        _client_entry_for_listing(cid, cfg, orgs_raw, strats_raw)
        for cid, cfg in cast(dict[str, ClientConfig], clients_cfg).items()
    ]
    return {
        "clients": clients,
        "organisations": _project_orgs_for_listing(orgs_raw),
        "strategies": _project_strats_for_listing(strats_raw),
    }
