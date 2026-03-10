import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp

from unified_trading_library.core.audit_middleware import RequestAuditMiddleware

from client_reporting_api.api.routes.alerts import router as alerts_router
from client_reporting_api.api.routes.health import router as health_router
from client_reporting_api.api.routes.pnl import router as pnl_router
from client_reporting_api.api.routes.reports import router as reports_router
from client_reporting_api.api.routes.reports_stream import router as reports_stream_router
from client_reporting_api.auth import auth_cfg as _auth_cfg
from client_reporting_api.auth import verify_api_key
from client_reporting_api.metrics import PROCESSING_LATENCY, RECORDS_PROCESSED

logger = logging.getLogger(__name__)

_RequestResponseEndpoint = Callable[[Request], Awaitable[StarletteResponse]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Correlation-ID for every request."""

    async def dispatch(
        self, request: Request, call_next: _RequestResponseEndpoint
    ) -> StarletteResponse:
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

    async def dispatch(
        self, request: Request, call_next: _RequestResponseEndpoint
    ) -> StarletteResponse:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        status = "success" if response.status_code < 500 else "error"
        RECORDS_PROCESSED.labels(status=status).inc()
        PROCESSING_LATENCY.observe(duration)
        return response


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

# --- Unauthenticated health / metrics endpoints ---
app.include_router(health_router)

# --- Unauthenticated SSE streaming endpoints ---
app.include_router(reports_stream_router, prefix="/api/v1", tags=["Streaming"])

# --- Authenticated API routes (require API key) ---
# Add authenticated routers here as they are created, e.g.:
# _authenticated_router = APIRouter(dependencies=[Depends(verify_api_key)])
# _authenticated_router.include_router(reports.router, prefix="/api/v1/reports", tags=["Reports"])
# app.include_router(_authenticated_router)

# For now, apply auth as a global dependency since all non-health routes should
# be protected. New route routers should be added to the authenticated router above.
_authenticated_router = APIRouter(dependencies=[Depends(verify_api_key)])
_authenticated_router.include_router(reports_router)
_authenticated_router.include_router(pnl_router)
_authenticated_router.include_router(alerts_router)
app.include_router(_authenticated_router)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
