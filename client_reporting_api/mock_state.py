"""Stateful mock store for client-reporting-api.

Initializes MockStateStore with seed data from mock_data.py so that
POST/PUT/DELETE mutations are reflected in subsequent GET responses
when CLOUD_MOCK_MODE=true.
"""

from __future__ import annotations

from unified_trading_library.core.mock_state_store import MockStateStore

from client_reporting_api.mock_data import MOCK_REPORTS

_store = MockStateStore("client-reporting-api")


def _ensure_id(
    items: list[dict[str, str | int | float | bool]],
    id_field: str,
) -> list[dict[str, object]]:
    """Copy the domain-specific id field to 'id' for MockStateStore keying."""
    return [{**item, "id": item[id_field]} for item in items]


_store.seed("reports", _ensure_id(MOCK_REPORTS, "report_id"))


def get_store() -> MockStateStore:
    """Return the module-level MockStateStore singleton."""
    return _store
