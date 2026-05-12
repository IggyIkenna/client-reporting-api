"""Invoice routes package.

The original ``invoices.py`` was split into focused sub-modules and
re-aggregated here into a single ``router`` that matches the original prefix
(``/api/v1/invoices``). Consumers continue to import ``router`` from this
package unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from client_reporting_api.api.routes.invoices import _mock_seed
from client_reporting_api.api.routes.invoices._shared import cloud_cfg as _cloud_cfg
from client_reporting_api.api.routes.invoices.analytics import router as analytics_router
from client_reporting_api.api.routes.invoices.dashboards import router as dashboards_router
from client_reporting_api.api.routes.invoices.generation import router as generation_router
from client_reporting_api.api.routes.invoices.portals import router as portals_router
from client_reporting_api.api.routes.invoices.transitions import router as transitions_router
from client_reporting_api.api.routes.invoices.viewing import router as viewing_router

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])
router.include_router(generation_router)
router.include_router(transitions_router)
router.include_router(dashboards_router)
router.include_router(portals_router)
router.include_router(viewing_router)
router.include_router(analytics_router)

__all__ = ["_cloud_cfg", "router"]
