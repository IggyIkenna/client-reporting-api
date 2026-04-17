"""GET /clients — client / organisation / strategy dropdown data."""

from __future__ import annotations

from fastapi import APIRouter

from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()


def _client_entry_for_listing(
    cid: str,
    cfg: dict[str, object],
    orgs_raw: object,
    strats_raw: object,
) -> dict[str, object]:
    """Project a client config row to the dict shape the UI selector wants."""
    org_id = str(cfg.get("organisation_id", ""))
    org_info = orgs_raw.get(org_id, {}) if isinstance(orgs_raw, dict) else {}
    strat_id = str(cfg.get("strategy_id", ""))
    strat_info = strats_raw.get(strat_id, {}) if isinstance(strats_raw, dict) else {}
    return {
        "id": cid,
        "name": str(cfg.get("full_name", cid)),
        "venue": str(cfg.get("venue", "")),
        "currency": str(cfg.get("currency", "")),
        "tranche": str(cfg.get("tranche", "")),
        "is_active": bool(cfg.get("is_active", False)),
        "is_underwater": bool(cfg.get("is_underwater", False)),
        "organisation_id": org_id,
        "organisation_name": (
            str(org_info.get("name", org_id)) if isinstance(org_info, dict) else org_id
        ),
        "organisation_type": (
            str(org_info.get("type", "client")) if isinstance(org_info, dict) else "client"
        ),
        "strategy_id": strat_id,
        "strategy_name": (
            str(strat_info.get("name", strat_id)) if isinstance(strat_info, dict) else strat_id
        ),
    }


def _project_orgs_for_listing(orgs_raw: object) -> list[dict[str, object]]:
    """Flatten the organisations section of the registry for the UI."""
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
def get_clients() -> dict[str, list[dict[str, object]]]:
    """Return all clients, organisations, and strategies from credentials-registry."""
    registry = load_registry()
    clients_cfg = registry.get("clients", {})
    orgs_raw = registry.get("organisations", {})
    strats_raw = registry.get("strategies", {})

    clients: list[dict[str, object]] = [
        _client_entry_for_listing(cid, cfg, orgs_raw, strats_raw)
        for cid, cfg in (clients_cfg.items() if isinstance(clients_cfg, dict) else [])
        if isinstance(cfg, dict)
    ]
    return {
        "clients": clients,
        "organisations": _project_orgs_for_listing(orgs_raw),
        "strategies": _project_strats_for_listing(strats_raw),
    }
