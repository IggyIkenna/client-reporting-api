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

_DATA_PATH = Path(__file__).resolve().parent / "static" / "investor_relations_archive_metadata.json"


@router.get("/investor-relations/archive-metadata")
def get_investor_relations_archive_metadata(auth: AuthDep) -> dict[str, object]:
    """Return investor-relations deck metadata for portal merge (current + archive rows).

    Source of truth is ``static/investor_relations_archive_metadata.json`` beside this
    module — deliberately NOT under a ``data/`` directory: the repo-wide ``.gitignore``'s
    ``data/`` pattern is for repo-local scratch/generated data (backfill caches, reports,
    charts), not committed reference config, so a prior ``data/...`` path here was silently
    gitignored and never committed (2026-08-21 side-discovery fix). Content mirrors the
    presentations statically declared in ``unified-trading-system-ui``'s
    ``app/(platform)/investor-relations/page.tsx`` — this backend copy is the intended
    single source of truth per this docstring; the UI's static array is a client-side
    fallback for when this route is unreachable.

    Entitlement: this is a cross-client reference catalogue (deck/archive
    metadata), not a per-client read — no client_id concept exists on
    this route, so ``require_internal`` is the strictest plausible gate
    (2026-08-21 CTO handoff P1 fix).
    """
    require_internal(auth)
    raw = _DATA_PATH.read_text(encoding="utf-8")
    return json.loads(raw)
