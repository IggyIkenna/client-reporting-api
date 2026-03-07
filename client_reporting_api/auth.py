"""Authentication for client-reporting-api.

Provides API key authentication via X-API-Key header and retains the
Google OAuth middleware for future OIDC migration.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from unified_config_interface import UnifiedCloudConfig
from unified_events_interface import log_event

import client_reporting_api._google_auth_sync as _google_auth_sync

GoogleAuthError = _google_auth_sync.GoogleAuthError
make_http_request = _google_auth_sync.make_http_request
verify_oauth2_token_sync = _google_auth_sync.verify_oauth2_token_sync

logger = logging.getLogger(__name__)

# --- Production guard for DISABLE_AUTH ---
_auth_cfg = UnifiedCloudConfig()
_disable_auth_raw = _auth_cfg.disable_auth
_environment = _auth_cfg.environment
if _disable_auth_raw and _environment == "production":
    logging.getLogger(__name__).critical("DISABLE_AUTH=true is forbidden in production. Auth remains ENABLED.")
    _effective_disable_auth: bool = False
else:
    _effective_disable_auth = _disable_auth_raw
DISABLE_AUTH: bool = _effective_disable_auth

# ---------------------------------------------------------------------------
# API key authentication (primary auth gate)
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: str | None = Security(api_key_header),
) -> str:
    """Validate the X-API-Key header against the API_KEY env var.

    Set DISABLE_AUTH=true for local development (defaults to false).
    """
    if DISABLE_AUTH:
        return "dev-mode"
    if not api_key:
        log_event("AUTH_FAILURE", severity="WARNING", details={"auth_type": "api_key", "reason": "missing_key"})
        raise HTTPException(status_code=401, detail="Missing API key")
    expected_key = _auth_cfg.api_key
    if not expected_key or api_key != expected_key:
        log_event("AUTH_FAILURE", severity="WARNING", details={"auth_type": "api_key", "reason": "invalid_key"})
        raise HTTPException(status_code=401, detail="Invalid API key")
    logger.info("Authentication successful: auth_type=api_key")
    return api_key


# ---------------------------------------------------------------------------
# Google OAuth middleware (retained for future OIDC migration)
# ---------------------------------------------------------------------------

_PASSTHROUGH_PATHS = frozenset({"/health", "/metrics", "/docs", "/openapi.json", "/redoc"})


class GoogleOAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates Google OAuth Bearer tokens.

    Token validation uses google.oauth2.id_token.verify_oauth2_token().
    client_id must come from Secret Manager via get_secret_client()
    using secret name 'google-oauth-client-id'. Pass at app startup.
    """

    def __init__(
        self,
        app: object,
        client_id: str,
        allowed_domains: list[str] | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._client_id = client_id
        self._allowed_domains = allowed_domains or []
        self._http_request = make_http_request()
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def dispatch(self, request: Request, call_next: object) -> Response:
        if request.url.path in _PASSTHROUGH_PATHS:
            return await call_next(request)  # type: ignore[operator,misc]

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            log_event(
                "AUTH_FAILURE", severity="WARNING", details={"auth_type": "oauth", "reason": "missing_bearer_token"}
            )
            return JSONResponse({"detail": "Missing Bearer token"}, status_code=401)

        token = auth_header[len("Bearer ") :]
        loop = asyncio.get_event_loop()
        try:
            claims: dict[str, object] = await loop.run_in_executor(
                self._executor,
                _google_auth_sync.verify_oauth2_token_sync,
                token,
                self._http_request,
                self._client_id,
            )
        except (GoogleAuthError, ValueError) as exc:
            logger.warning("Token validation failed: %s", exc)
            log_event("AUTH_FAILURE", severity="WARNING", details={"auth_type": "oauth", "reason": "invalid_token"})
            return JSONResponse({"detail": "Invalid token"}, status_code=401)

        if self._allowed_domains:
            hd_raw = claims.get("hd")
            hd = str(hd_raw) if hd_raw is not None else ""
            if hd not in self._allowed_domains:
                logger.warning("Domain %r not in allowed list %r", hd, self._allowed_domains)
                log_event(
                    "AUTH_FAILURE", severity="WARNING", details={"auth_type": "oauth", "reason": "domain_not_allowed"}
                )
                return JSONResponse({"detail": "Domain not allowed"}, status_code=403)

        logger.info("Authentication successful: auth_type=oauth")
        return await call_next(request)  # type: ignore[operator,misc]
