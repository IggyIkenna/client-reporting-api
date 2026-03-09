import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

# TODO(GH-BACKLOG): PrometheusMiddleware and get_metrics_response are not yet implemented in
# unified_trading_library. Re-enable once unified-trading-library adds observability
# middleware support (track in UTL backlog).
# from unified_trading_library import PrometheusMiddleware, get_metrics_response
from client_reporting_api.api.routes.health import router as health_router
from client_reporting_api.api.routes.reports_stream import router as reports_stream_router
from client_reporting_api.auth import _auth_cfg, verify_api_key

logger = logging.getLogger(__name__)

_RequestResponseEndpoint = Callable[[Request], Awaitable[StarletteResponse]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Correlation-ID for every request."""

    async def dispatch(self, request: Request, call_next: _RequestResponseEndpoint) -> StarletteResponse:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


_env = _auth_cfg.environment
app = FastAPI(
    title="Client Reporting Service",
    version="1.0.0",
    docs_url="/docs" if _env != "production" else None,
    redoc_url="/redoc" if _env != "production" else None,
    openapi_url="/openapi.json" if _env != "production" else None,
)
# TODO(GH-BACKLOG): Re-enable once PrometheusMiddleware is available in unified_trading_library.
# app.add_middleware(PrometheusMiddleware, service_name="client-reporting-api")

app.add_middleware(CorrelationIdMiddleware)

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
app.include_router(_authenticated_router)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
