"""Seed data for invoice mock mode.

Loaded once at package import and merged into the shared in-memory store so
the UI has something to render before any real invoice has been generated.
"""

from __future__ import annotations

from client_reporting_api.api.routes.invoices._shared import store

MOCK_INVOICES: list[dict[str, str | int | float | bool]] = [
    {
        "invoice_id": "INV-2026-001",
        "client_id": "PR",
        "org_id": "org-alpha",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "issued",
        "currency": "USD",
        "subtotal": 4820.00,
        "tax": 0.00,
        "total": 4820.00,
        "description": "Performance Fee - Prism Capital (Mar 2026)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 300000.00,
        "closing_aum": 324100.00,
        "pnl": 24100.00,
        "trader_hwm_before": 300000.00,
        "odum_hwm_before": 300000.00,
        "trader_fee": 4820.00,
        "odum_fee": 1205.00,
        "is_underwater": False,
    },
    {
        "invoice_id": "INV-2026-002",
        "client_id": "ET",
        "org_id": "org-alpha",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "paid",
        "currency": "USD",
        "subtotal": 3440.00,
        "tax": 0.00,
        "total": 3440.00,
        "description": "Performance Fee - Eqvilent (Mar 2026)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 2020000.00,
        "closing_aum": 2037200.00,
        "pnl": 17200.00,
        "trader_hwm_before": 2020000.00,
        "odum_hwm_before": 2020000.00,
        "trader_fee": 3440.00,
        "odum_fee": 860.00,
        "is_underwater": False,
    },
    {
        "invoice_id": "INV-2026-003",
        "client_id": "GP",
        "org_id": "org-beta",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "issued",
        "currency": "USD",
        "subtotal": 50.00,
        "tax": 0.00,
        "total": 50.00,
        "description": "Server Cost - GPD Capital (underwater)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 409000.00,
        "closing_aum": 406955.00,
        "pnl": -2045.00,
        "trader_hwm_before": 420000.00,
        "odum_hwm_before": 415000.00,
        "trader_fee": 0.00,
        "odum_fee": 0.00,
        "server_cost": 50.00,
        "is_underwater": True,
    },
]


def _seed_mock_invoices() -> None:
    """Insert or update every ``MOCK_INVOICES`` entry into the store.

    Seed always wins over cached dev-session mutations so tests see
    the canonical seed state regardless of accumulated local cache.
    """
    for inv in MOCK_INVOICES:
        inv_data = {**inv, "id": inv["invoice_id"]}
        existing = store.get("invoices", str(inv["invoice_id"]))
        if existing is None:
            store.create("invoices", inv_data)
        else:
            store.update("invoices", str(inv["invoice_id"]), inv_data)


_seed_mock_invoices()
