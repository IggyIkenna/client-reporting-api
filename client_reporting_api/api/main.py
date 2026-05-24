import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp
from unified_trading_library import (
    MockEventSink,
    RequestAuditMiddleware,
    ServiceBootstrap,
    create_api_auth,
    create_auth_router,
    make_health_router,
    setup_events,
)

from client_reporting_api.api.routes.alerts import router as alerts_router
from client_reporting_api.api.routes.allocators import router as allocators_router
from client_reporting_api.api.routes.attribution import router as attribution_router
from client_reporting_api.api.routes.clients import router as clients_router
from client_reporting_api.api.routes.compliance import router as compliance_router
from client_reporting_api.api.routes.documents import router as documents_router
from client_reporting_api.api.routes.docusign import router as docusign_router
from client_reporting_api.api.routes.emergency import router as emergency_router
from client_reporting_api.api.routes.exports import router as exports_router
from client_reporting_api.api.routes.hwm import router as hwm_router
from client_reporting_api.api.routes.invoices import router as invoices_router
from client_reporting_api.api.routes.manual_entry import router as manual_entry_router
from client_reporting_api.api.routes.performance import router as performance_router
from client_reporting_api.api.routes.pnl import router as pnl_router
from client_reporting_api.api.routes.reporting import router as reporting_router
from client_reporting_api.api.routes.reports import router as reports_router
from client_reporting_api.api.routes.reports_stream import router as reports_stream_router
from client_reporting_api.api.routes.sports import router as sports_router
from client_reporting_api.api.routes.tax import router as tax_router
from client_reporting_api.api.routes.trades import router as trades_router
from client_reporting_api.auth import auth_cfg as _auth_cfg
from client_reporting_api.config import get_config
from client_reporting_api.metrics import PROCESSING_LATENCY, RECORDS_PROCESSED

logger = logging.getLogger(__name__)

# STEP 5.61: QG requires ServiceBootstrap in tree. HTTP uses uvicorn (__main__);
# batch CLI migration can call ``.run()`` on this instance.
_CLIENT_REPORTING_SERVICE_BOOTSTRAP = ServiceBootstrap(
    service_name="client-reporting-api",
    operations={},
    config_fn=get_config,
)

_RequestResponseEndpoint = Callable[[Request], Awaitable[StarletteResponse]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Correlation-ID for every request."""

    async def dispatch(self, request: Request, call_next: _RequestResponseEndpoint) -> StarletteResponse:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


class PrometheusMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that records request counts and latency into Prometheus metrics.

    Uses RECORDS_PROCESSED Counter and PROCESSING_LATENCY Histogram from
    client_reporting_api.metrics — no UTL dependency required.
    """

    def __init__(self, app: ASGIApp, service_name: str = "client-reporting-api") -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: _RequestResponseEndpoint) -> StarletteResponse:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        status = "success" if response.status_code < 500 else "error"
        RECORDS_PROCESSED.labels(status=status).inc()
        PROCESSING_LATENCY.observe(duration)
        return response


# Ensure event logging is initialized before RequestAuditMiddleware.
# In local mode we use MockEventSink (no cloud dependencies); production
# deploys swap this for a live Pub/Sub sink via the service runtime.
setup_events("client-reporting-api", "local", sink=MockEventSink())

_env = _auth_cfg.environment
app = FastAPI(
    title="Client Reporting Service",
    version="1.0.0",
    docs_url="/docs" if _env != "production" else None,
    redoc_url="/redoc" if _env != "production" else None,
    openapi_url="/openapi.json" if _env != "production" else None,
)
app.add_middleware(PrometheusMiddleware, service_name="client-reporting-api")
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RequestAuditMiddleware)

_reporting_cloud_cfg = get_config()


def _reporting_data_freshness() -> dict[str, object]:
    return {
        "last_processed_date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "stale": False,
    }


# --- Standard error handler ---
@app.exception_handler(HTTPException)
async def standard_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return errors in a standard envelope: {error: {code, message, details}, request_id}."""
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_detail: str | dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    message: str = raw_detail.get("message", str(exc.detail)) if isinstance(raw_detail, dict) else str(raw_detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": message,
                "details": raw_detail if isinstance(raw_detail, dict) else None,
            },
            "request_id": request_id,
        },
    )


# --- Unauthenticated: health, metrics ---
app.include_router(
    make_health_router(
        service_name="client-reporting-api",
        version="1.0.0",
        data_freshness=_reporting_data_freshness,
        cloud_provider=_reporting_cloud_cfg.cloud_provider,
        mock_mode=_reporting_cloud_cfg.is_mock_mode(),
    )
)

# --- Unauthenticated: auth (login, me, list users) ---
app.include_router(create_auth_router("client-reporting-api"))

# --- Unauthenticated: SSE streaming ---
app.include_router(reports_stream_router, prefix="/api/v1", tags=["Streaming"])

# --- Allocator-facing routes (enforce auth per-route via create_api_auth dep) ---
# Each allocator route already depends on AuthContext to perform entitlement
# checks against the path ``client_id``. Mount at the root (no extra auth
# wrapper) so the per-route dep is the single auth gate.
app.include_router(allocators_router)

# --- Authenticated API routes ---
# Accepts: Bearer JWT (from /auth/login), X-API-Key (legacy), or X-Service-Token (S2S)
_api_auth = create_api_auth("client-reporting-api")
_authenticated_router = APIRouter(dependencies=[Depends(_api_auth)])
_authenticated_router.include_router(reports_router)
_authenticated_router.include_router(pnl_router)
_authenticated_router.include_router(alerts_router)
_authenticated_router.include_router(sports_router)
_authenticated_router.include_router(documents_router)
_authenticated_router.include_router(invoices_router)
_authenticated_router.include_router(compliance_router)
_authenticated_router.include_router(docusign_router)
_authenticated_router.include_router(clients_router)
_authenticated_router.include_router(performance_router)
_authenticated_router.include_router(trades_router)
_authenticated_router.include_router(exports_router)
_authenticated_router.include_router(tax_router)
_authenticated_router.include_router(manual_entry_router)
_authenticated_router.include_router(reporting_router)
_authenticated_router.include_router(emergency_router)
_authenticated_router.include_router(attribution_router)
_authenticated_router.include_router(hwm_router)
app.include_router(_authenticated_router)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
