"""GET /api/reporting/investor-relations/archive-metadata — IR deck catalogue (read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.entitlement import require_internal

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_DATA_PATH = Path(__file__).resolve().parent / "data" / "investor_relations_archive_metadata.json"


@router.get("/investor-relations/archive-metadata")
def get_investor_relations_archive_metadata(auth: AuthDep) -> dict[str, object]:
    """Return investor-relations deck metadata for portal merge (current + archive rows).

    Source of truth is ``data/investor_relations_archive_metadata.json`` beside this module.

    Entitlement: this is a cross-client reference catalogue (deck/archive
    metadata), not a per-client read — no client_id concept exists on
    this route, so ``require_internal`` is the strictest plausible gate
    (2026-08-21 CTO handoff P1 fix).
    """
    require_internal(auth)
    raw = _DATA_PATH.read_text(encoding="utf-8")
    return json.loads(raw)
