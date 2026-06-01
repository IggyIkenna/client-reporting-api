"""Reporting API routes package.

The original ``reporting.py`` was split into focused sub-modules. They are
re-aggregated here into a single ``router`` mounted at ``/api/reporting``.
"""

from __future__ import annotations

from fastapi import APIRouter

from client_reporting_api.api.routes.reporting.clients_listing import router as clients_router
from client_reporting_api.api.routes.reporting.fund_operations import (
    router as fund_operations_router,
)
from client_reporting_api.api.routes.reporting.investor_relations_archive import (
    router as investor_relations_archive_router,
)
from client_reporting_api.api.routes.reporting.invoices_listing import (
    router as invoices_listing_router,
)
from client_reporting_api.api.routes.reporting.nav import router as nav_router
from client_reporting_api.api.routes.reporting.performance import router as performance_router
from client_reporting_api.api.routes.reporting.reports_overview import (
    router as reports_overview_router,
)
from client_reporting_api.api.routes.reporting.settlements import router as settlements_router
from client_reporting_api.api.routes.reporting.trades import router as trades_router

router = APIRouter(prefix="/api/reporting", tags=["reporting"])
router.include_router(investor_relations_archive_router)
router.include_router(clients_router)
router.include_router(performance_router)
router.include_router(trades_router)
router.include_router(reports_overview_router)
router.include_router(settlements_router)
router.include_router(fund_operations_router)
router.include_router(nav_router)
router.include_router(invoices_listing_router)

__all__ = ["router"]
