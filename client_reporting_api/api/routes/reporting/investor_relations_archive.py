"""GET /api/reporting/investor-relations/archive-metadata — IR deck catalogue (read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

_DATA_PATH = Path(__file__).resolve().parent / "data" / "investor_relations_archive_metadata.json"


@router.get("/investor-relations/archive-metadata")
def get_investor_relations_archive_metadata() -> dict[str, Any]:
    """Return investor-relations deck metadata for portal merge (current + archive rows).

    Source of truth is ``data/investor_relations_archive_metadata.json`` beside this module.
    """
    raw = _DATA_PATH.read_text(encoding="utf-8")
    return json.loads(raw)
