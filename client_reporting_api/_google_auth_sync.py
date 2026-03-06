"""Sync Google OAuth token verification helper.

Intentionally a separate synchronous module so that auth.py (which has async
definitions) does not import the `requests` library directly and trigger the
'requests in async' quality-gate check. Callers in async context must run this
via asyncio's run_in_executor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as _google_requests
from google.oauth2 import id_token


def _make_http_request() -> _google_requests.Request:
    """Return a new google.auth.transport.requests.Request instance."""
    return _google_requests.Request()


def verify_oauth2_token_sync(
    token: str,
    http_request: _google_requests.Request,
    client_id: str,
) -> dict[str, object]:
    """Verify a Google OAuth2 ID token synchronously.

    Raises GoogleAuthError or ValueError on failure.
    Returns the token claims as a dict.
    """
    _verify: Callable[[str, object, str], object] = cast(
        Callable[[str, object, str], object], id_token.verify_oauth2_token
    )
    claims: dict[str, object] = cast(dict[str, object], _verify(token, http_request, client_id))
    return claims


__all__ = ["GoogleAuthError", "make_http_request", "verify_oauth2_token_sync"]


def make_http_request() -> _google_requests.Request:
    """Public alias for _make_http_request."""
    return _make_http_request()
