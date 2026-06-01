"""Entitlement helpers for client-reporting-api routes.

This module is the single source of truth for per-route authorisation on
top of the blanket ``create_api_auth`` gate that already validates the
caller's token. Two helpers are exposed:

* :func:`enforce_entitlement` — reject external callers whose
  ``AuthContext.org_id`` does not match the ``client_id`` they are
  trying to read. Internal callers (``is_internal=True``) bypass the
  check, matching the allocator-routes semantics shipped in Phase 4 of
  ``fund_administration_service_and_pooled_subscription_redemption``.
* :func:`require_internal` — refuse non-internal callers outright.
  Used on ops / reconciliation / trading-control endpoints where
  external clients should never have read or write access regardless of
  ``client_id``.

Both helpers emit a ``logger.warning`` on deny so the standard request
log captures the attempt. A structured ``REPORTING_ENTITLEMENT_DENIED``
lifecycle event is tracked as a follow-up once the event type lands in
``unified_trading_library.events.event_types``; until then the warning
log is the audit trail. See the ``cra_entitlement_backfill`` plan.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from unified_trading_library import AuthContext

logger = logging.getLogger(__name__)


def enforce_entitlement(auth: AuthContext, client_id: str) -> None:
    """Reject with 403 when the caller's entitled client does not match.

    Internal callers (admin / S2S / local dev) bypass the check — they
    need cross-client visibility for reconciliation and support.
    ``AuthContext.org_id`` is the entitled client id for external users.

    Args:
        auth: Resolved ``AuthContext`` for the current request.
        client_id: The ``client_id`` path/query parameter the caller is
            attempting to read.

    Raises:
        HTTPException: 403 if the caller is external and ``org_id`` !=
            ``client_id``.
    """
    if auth.is_internal:
        return
    if auth.org_id != client_id:
        logger.warning(
            "Entitlement denied: caller_org=%s path_client=%s",
            auth.org_id,
            client_id,
        )
        # TODO: emit REPORTING_ENTITLEMENT_DENIED once UTL event is registered.
        raise HTTPException(
            status_code=403,
            detail="Not entitled to read this client",
        )


def require_internal(auth: AuthContext) -> None:
    """Reject with 403 when the caller is not an internal / admin user.

    Use on endpoints that expose cross-client aggregates, trading
    controls, invoice lifecycle operations, or any other surface that
    should never be reachable by external client API keys regardless of
    the ``client_id`` they pass.

    Args:
        auth: Resolved ``AuthContext`` for the current request.

    Raises:
        HTTPException: 403 if ``auth.is_internal`` is ``False``.
    """
    if auth.is_internal:
        return
    logger.warning(
        "Internal-only route denied: caller_org=%s user=%s role=%s",
        auth.org_id,
        auth.user_id,
        auth.role,
    )
    # TODO: emit REPORTING_ENTITLEMENT_DENIED once UTL event is registered.
    raise HTTPException(
        status_code=403,
        detail="Internal-only endpoint",
    )


__all__ = ["enforce_entitlement", "require_internal"]
